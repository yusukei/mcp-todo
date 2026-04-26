//! Web Terminal PTY support.
//!
//! Rust port of `agent/main.py:PtySession` (~1666-1843) and
//! `PtyManager` (~1844-2170). Uses `portable-pty` for the cross-platform
//! PTY abstraction (Unix posix_openpt, Windows ConPTY) so we don't need
//! pywinpty or a hand-rolled winpty bridge.
//!
//! Protocol (matches Python):
//! - `terminal_create` → `terminal_create_result` ({success, session_id, shell})
//! - `terminal_input`  → no response
//! - `terminal_resize` → no response
//! - `terminal_kill`   → `terminal_kill_result`
//! - `terminal_list`   → `terminal_list_result` ({sessions: [{...}]})
//! - `terminal_attach` → `terminal_attach_result` ({success, scrollback: [...]})
//! - `terminal_detach` → no-op (browser disconnect; session keeps running)
//! - `terminal_close`  → legacy alias for detach
//! - `terminal_output` → server-push, base64 chunks (one per read)
//! - `terminal_exit`   → server-push when shell exits
//!
//! Sessions persist across browser disconnects: only `terminal_kill`
//! or the shell itself exiting end a session. The scrollback ring lets
//! a reconnecting browser restore screen state via `terminal_attach`.

pub mod session;

use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::SystemTime;

use serde_json::{json, Value};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;
use tracing::{debug, info, warn};

use self::session::{spawn_session, PtySession};

const SCROLLBACK_DEFAULT: usize = 10_000;
const SCROLLBACK_MIN: usize = 100;
const SCROLLBACK_ENV: &str = "MCP_TERMINAL_SCROLLBACK";

/// Scrollback chunk — base64-encoded PTY output, plus the timestamp
/// the agent received it. Matches the Python wire shape so the
/// frontend's existing replay code keeps working.
#[derive(Debug, Clone)]
struct ScrollChunk {
    /// Base64-encoded raw PTY bytes (the same shape as `terminal_output`'s
    /// `data` field on the wire).
    data: String,
    /// Unix timestamp (seconds with fractional ms) the chunk was captured.
    ts: f64,
}

#[derive(Debug)]
struct SessionState {
    session: Arc<PtySession>,
    scrollback: Mutex<VecDeque<ScrollChunk>>,
    started_at: f64,
    last_activity: Mutex<f64>,
    cmdline: String,
    exited: Mutex<bool>,
}

type Registry = Arc<Mutex<HashMap<String, Arc<SessionState>>>>;

fn registry() -> &'static Registry {
    static R: OnceLock<Registry> = OnceLock::new();
    R.get_or_init(|| Arc::new(Mutex::new(HashMap::new())))
}

fn scrollback_max() -> usize {
    std::env::var(SCROLLBACK_ENV)
        .ok()
        .and_then(|s| s.parse::<usize>().ok())
        .map(|n| n.max(SCROLLBACK_MIN))
        .unwrap_or(SCROLLBACK_DEFAULT)
}

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// PTY-aware dispatcher: terminal_input / resize / detach return None
/// (no response), the others return a (response_type, payload) pair
/// just like the regular handlers. Distinct from `handlers::dispatch`
/// because some terminal operations need access to `out_tx` to push
/// `terminal_output` server frames during the lifetime of the session.
pub async fn dispatch_terminal(
    request_type: &str,
    payload: Value,
    out_tx: mpsc::Sender<Message>,
) -> Option<(String, Value)> {
    match request_type {
        "terminal_create" => {
            let v = handle_terminal_create(payload, out_tx).await;
            Some(("terminal_create_result".into(), v))
        }
        "terminal_input" => {
            handle_terminal_input(payload).await;
            None
        }
        "terminal_resize" => {
            handle_terminal_resize(payload).await;
            None
        }
        "terminal_kill" => {
            let v = handle_terminal_kill(payload).await;
            Some(("terminal_kill_result".into(), v))
        }
        "terminal_list" => {
            let v = handle_terminal_list().await;
            Some(("terminal_list_result".into(), v))
        }
        "terminal_attach" => {
            let v = handle_terminal_attach(payload).await;
            Some(("terminal_attach_result".into(), v))
        }
        // Browser disconnect — session persists.
        "terminal_detach" | "terminal_close" => {
            debug!(?payload, "terminal_detach/close: no-op (session persists)");
            None
        }
        _ => None,
    }
}

async fn handle_terminal_create(
    payload: Value,
    out_tx: mpsc::Sender<Message>,
) -> Value {
    let session_id = payload
        .get("session_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if session_id.is_empty() {
        return json!({"success": false, "error": "session_id required"});
    }
    {
        let reg = registry().lock().unwrap();
        if reg.contains_key(&session_id) {
            return json!({"success": false, "error": "session_id already exists"});
        }
    }

    let shell_hint = payload
        .get("shell")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let cols = payload.get("cols").and_then(Value::as_u64).unwrap_or(120) as u16;
    let rows = payload.get("rows").and_then(Value::as_u64).unwrap_or(40) as u16;
    let cwd = payload
        .get("cwd")
        .and_then(Value::as_str)
        .map(str::to_owned);

    let shell = resolve_shell(&shell_hint);
    let session = match spawn_session(&shell, cols, rows, cwd.as_deref()) {
        Ok(s) => Arc::new(s),
        Err(e) => {
            warn!(error = %e, %session_id, "PTY spawn failed");
            return json!({"success": false, "error": format!("spawn failed: {e}")});
        }
    };

    let state = Arc::new(SessionState {
        session: session.clone(),
        scrollback: Mutex::new(VecDeque::with_capacity(SCROLLBACK_DEFAULT)),
        started_at: now_secs(),
        last_activity: Mutex::new(now_secs()),
        cmdline: shell.clone(),
        exited: Mutex::new(false),
    });
    registry()
        .lock()
        .unwrap()
        .insert(session_id.clone(), state.clone());

    // Reader: pull bytes from the PTY in a blocking thread, push as
    // base64 `terminal_output` frames to the WS sender, append to
    // scrollback, and emit `terminal_exit` on EOF.
    let session_id_for_reader = session_id.clone();
    let state_for_reader = state.clone();
    let session_for_reader = session.clone();
    let max = scrollback_max();
    tokio::task::spawn_blocking(move || {
        let mut buf = vec![0u8; 64 * 1024];
        loop {
            let n = match session_for_reader.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => n,
                Err(e) => {
                    debug!(error = %e, "PTY read ended");
                    break;
                }
            };
            let chunk = base64::Engine::encode(
                &base64::engine::general_purpose::STANDARD,
                &buf[..n],
            );
            let ts = now_secs();
            // Bound the scrollback ring.
            let mut sb = state_for_reader.scrollback.lock().unwrap();
            if sb.len() >= max {
                sb.pop_front();
            }
            sb.push_back(ScrollChunk {
                data: chunk.clone(),
                ts,
            });
            drop(sb);
            *state_for_reader.last_activity.lock().unwrap() = ts;

            let frame = json!({
                "type": "terminal_output",
                "payload": {
                    "session_id": session_id_for_reader,
                    "data": chunk,
                },
            });
            let serialized = match serde_json::to_string(&frame) {
                Ok(s) => s,
                Err(_) => continue,
            };
            // The send is from a blocking thread — use blocking_send.
            // Channel-closed means the WS reconnected; the session
            // keeps running and the next attach replays scrollback.
            if out_tx
                .blocking_send(Message::Text(serialized.into()))
                .is_err()
            {
                debug!("out channel closed; pty_output dropped (session continues)");
            }
        }
        // Mark exited and emit `terminal_exit`.
        *state_for_reader.exited.lock().unwrap() = true;
        let exit_frame = json!({
            "type": "terminal_exit",
            "payload": {
                "session_id": session_id_for_reader,
                "exit_code": -1,
            },
        });
        if let Ok(s) = serde_json::to_string(&exit_frame) {
            let _ = out_tx.blocking_send(Message::Text(s.into()));
        }
    });

    info!(%session_id, %shell, %cols, %rows, "PTY session created");
    json!({
        "success": true,
        "session_id": session_id,
        "shell": shell,
    })
}

async fn handle_terminal_input(payload: Value) {
    let session_id = match payload.get("session_id").and_then(Value::as_str) {
        Some(s) => s.to_string(),
        None => return,
    };
    let data = payload
        .get("data")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let state = registry().lock().unwrap().get(&session_id).cloned();
    if let Some(state) = state {
        // `data` is base64-encoded raw bytes. Decode then write.
        let bytes = match base64::Engine::decode(
            &base64::engine::general_purpose::STANDARD,
            data.as_bytes(),
        ) {
            Ok(b) => b,
            // Frontend may send already-utf8 strings on legacy paths;
            // fall through to writing the raw text.
            Err(_) => data.into_bytes(),
        };
        if let Err(e) = state.session.write(&bytes) {
            warn!(%session_id, error = %e, "PTY write failed");
        }
        *state.last_activity.lock().unwrap() = now_secs();
    }
}

async fn handle_terminal_resize(payload: Value) {
    let session_id = match payload.get("session_id").and_then(Value::as_str) {
        Some(s) => s.to_string(),
        None => return,
    };
    let cols = payload.get("cols").and_then(Value::as_u64).unwrap_or(120) as u16;
    let rows = payload.get("rows").and_then(Value::as_u64).unwrap_or(40) as u16;
    let state = registry().lock().unwrap().get(&session_id).cloned();
    if let Some(state) = state {
        if let Err(e) = state.session.resize(cols, rows) {
            warn!(%session_id, error = %e, "PTY resize failed");
        }
    }
}

async fn handle_terminal_kill(payload: Value) -> Value {
    let session_id = payload
        .get("session_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if session_id.is_empty() {
        return json!({"success": false, "error": "session_id required"});
    }
    let state = registry().lock().unwrap().remove(&session_id);
    let Some(state) = state else {
        return json!({"success": false, "error": "session not found"});
    };
    state.session.kill();
    *state.exited.lock().unwrap() = true;
    json!({
        "success": true,
        "session_id": session_id,
    })
}

async fn handle_terminal_list() -> Value {
    let reg = registry().lock().unwrap();
    let mut sessions = Vec::with_capacity(reg.len());
    for (id, state) in reg.iter() {
        let exited = *state.exited.lock().unwrap();
        sessions.push(json!({
            "session_id": id,
            "started_at": state.started_at,
            "last_activity": *state.last_activity.lock().unwrap(),
            "cmdline": state.cmdline,
            "alive": !exited,
        }));
    }
    json!({ "sessions": sessions })
}

async fn handle_terminal_attach(payload: Value) -> Value {
    let session_id = payload
        .get("session_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if session_id.is_empty() {
        return json!({"success": false, "error": "session_id required"});
    }
    let state = registry().lock().unwrap().get(&session_id).cloned();
    let Some(state) = state else {
        return json!({"success": false, "error": "session not found"});
    };
    let scrollback: Vec<Value> = state
        .scrollback
        .lock()
        .unwrap()
        .iter()
        .map(|c| {
            json!({
                "data": c.data,
                "ts": c.ts,
            })
        })
        .collect();
    json!({
        "success": true,
        "session_id": session_id,
        "scrollback": scrollback,
        "started_at": state.started_at,
        "last_activity": *state.last_activity.lock().unwrap(),
        "cmdline": state.cmdline,
        "exited": *state.exited.lock().unwrap(),
    })
}

fn resolve_shell(hint: &str) -> String {
    if !hint.is_empty() && std::path::Path::new(hint).exists() {
        return hint.to_string();
    }
    // Fall back to the agent's detect_shells (Phase 1 minimal version).
    if cfg!(windows) {
        std::env::var("COMSPEC").unwrap_or_else(|_| r"C:\Windows\system32\cmd.exe".into())
    } else if std::path::Path::new("/bin/zsh").exists() {
        "/bin/zsh".into()
    } else if std::path::Path::new("/bin/bash").exists() {
        "/bin/bash".into()
    } else {
        "/bin/sh".into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scrollback_max_respects_env_floor() {
        std::env::set_var(SCROLLBACK_ENV, "5");
        // Floor enforced — value too small clamps to MIN.
        assert_eq!(scrollback_max(), SCROLLBACK_MIN);
        std::env::remove_var(SCROLLBACK_ENV);
    }

    #[test]
    fn scrollback_max_default_when_unset() {
        std::env::remove_var(SCROLLBACK_ENV);
        assert_eq!(scrollback_max(), SCROLLBACK_DEFAULT);
    }

    #[test]
    fn scrollback_max_parses_valid() {
        std::env::set_var(SCROLLBACK_ENV, "5000");
        assert_eq!(scrollback_max(), 5000);
        std::env::remove_var(SCROLLBACK_ENV);
    }

    #[test]
    fn resolve_shell_falls_back() {
        // empty hint → fallback
        let s = resolve_shell("");
        assert!(!s.is_empty());
    }

    #[tokio::test]
    async fn terminal_create_requires_session_id() {
        let (tx, _rx) = mpsc::channel(8);
        let v = handle_terminal_create(json!({}), tx).await;
        assert_eq!(v["success"], false);
        assert!(v["error"]
            .as_str()
            .unwrap_or("")
            .contains("session_id"));
    }

    #[tokio::test]
    async fn terminal_kill_unknown_session() {
        let v =
            handle_terminal_kill(json!({"session_id": "totally-unknown-session-id"})).await;
        assert_eq!(v["success"], false);
        assert!(v["error"].as_str().unwrap_or("").contains("not found"));
    }

    #[tokio::test]
    async fn terminal_attach_unknown_session() {
        let v = handle_terminal_attach(
            json!({"session_id": "another-totally-unknown-id"}),
        )
        .await;
        assert_eq!(v["success"], false);
    }
}
