//! WebSocket client to the backend agent endpoint.
//!
//! Holds the long-lived connection to
//! ``/api/v1/workspaces/agent/ws`` and runs it under a reconnect loop
//! with exponential backoff (1s → 60s) + ±20 % jitter.
//!
//! Connection cycle:
//! 1. `connect_async` (rustls-native-roots).
//! 2. Send `{"type":"auth","token":...}`; wait for `auth_ok` (10 s timeout).
//! 3. Push `{"type":"agent_info", ...}` (hostname / host_id / os / shells / version).
//! 4. Spawn an application-level heartbeat task that emits
//!    `{"type":"ping"}` every 30 s. The Python agent picked
//!    application-level pings over WS-frame pings because uvicorn
//!    doesn't always reply to WS pings under load — so we mirror that
//!    choice for backend parity.
//! 5. Read frames in a loop and dispatch:
//!    - `auth_ok` / `auth_error` outside handshake → log + ignore
//!    - `pong` → log
//!    - `update_available` → defer to agent-rs/07
//!    - anything else → try as a [`RequestEnvelope`] and route to
//!      [`crate::handlers::dispatch`]; the response envelope (`{type,
//!      request_id, payload}`) is sent back over the same socket.
//! 6. On disconnect: abort heartbeat, close the socket, sleep with
//!    jitter, retry.

use std::sync::Arc;
use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use futures_util::{SinkExt, StreamExt};
use rand::Rng;
use tokio::sync::mpsc;
use tokio::time::{sleep, timeout};
use tokio_tungstenite::{connect_async, tungstenite::Message};
use tracing::{debug, info, warn};

use crate::handlers;
use crate::proto::{Incoming, Outgoing, RequestEnvelope, Response};
use crate::self_update;
use crate::version;

const RECONNECT_INITIAL_MS: u64 = 1_000;
const RECONNECT_MAX_MS: u64 = 60_000;
const RECONNECT_JITTER_PCT: f64 = 0.20;
const AUTH_OK_TIMEOUT: Duration = Duration::from_secs(10);
const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(30);
const OUT_CHANNEL_CAPACITY: usize = 64;

pub struct Client {
    url: String,
    token: String,
    /// Trips when self-update succeeds, telling the parent runtime to
    /// exit so the spawned replacement takes over.
    shutdown_after_update: Arc<tokio::sync::Notify>,
}

impl Client {
    pub fn new(url: String, token: String) -> Self {
        Self {
            url,
            token,
            shutdown_after_update: Arc::new(tokio::sync::Notify::new()),
        }
    }

    /// Returned to `main` so the parent task can `select!` on it
    /// alongside the OS shutdown signal.
    pub fn shutdown_signal(&self) -> Arc<tokio::sync::Notify> {
        self.shutdown_after_update.clone()
    }

    /// Run forever: connect, serve, reconnect on disconnect.
    pub async fn run(self) -> Result<()> {
        let me = Arc::new(self);
        let mut backoff_ms = RECONNECT_INITIAL_MS;
        loop {
            match me.connect_and_serve().await {
                Ok(()) => {
                    info!("ws disconnected cleanly; reconnecting");
                    backoff_ms = RECONNECT_INITIAL_MS;
                }
                Err(e) => {
                    warn!(
                        error = %format!("{e:#}"),
                        backoff_ms,
                        "ws error; reconnecting"
                    );
                }
            }
            sleep_with_jitter(backoff_ms).await;
            backoff_ms = backoff_ms.saturating_mul(2).min(RECONNECT_MAX_MS);
        }
    }

    async fn connect_and_serve(self: &Arc<Self>) -> Result<()> {
        info!(url = %self.url, "connecting to backend");
        let (ws_stream, _resp) =
            connect_async(&self.url).await.context("ws connect failed")?;
        let (mut write, mut read) = ws_stream.split();

        // ── Auth handshake ──
        let auth = Outgoing::Auth {
            token: self.token.clone(),
        };
        let auth_json = serde_json::to_string(&auth).context("serialize auth")?;
        write
            .send(Message::Text(auth_json.into()))
            .await
            .context("send auth")?;

        let agent_id = match timeout(AUTH_OK_TIMEOUT, read.next()).await {
            Ok(Some(Ok(Message::Text(s)))) => {
                let parsed: Incoming = serde_json::from_str(s.as_str())
                    .context("parse auth response")?;
                match parsed {
                    Incoming::AuthOk { agent_id } => agent_id,
                    Incoming::AuthError { message } => {
                        bail!("auth_error from backend: {}", message.unwrap_or_default())
                    }
                    other => bail!("expected auth_ok, got {other:?}"),
                }
            }
            Ok(Some(Ok(other))) => bail!("expected text auth_ok, got {:?}", other),
            Ok(Some(Err(e))) => bail!("ws read during auth: {e}"),
            Ok(None) => bail!("ws closed before auth_ok"),
            Err(_) => bail!("auth_ok timeout after {AUTH_OK_TIMEOUT:?}"),
        };
        info!(%agent_id, "authenticated");

        // ── agent_info push ──
        let info = Outgoing::AgentInfo {
            hostname: version::hostname(),
            host_id: version::host_id(),
            os: version::os_label().to_string(),
            shells: version::detect_shells(),
            agent_version: version::VERSION.to_string(),
        };
        let info_json = serde_json::to_string(&info).context("serialize agent_info")?;
        write
            .send(Message::Text(info_json.into()))
            .await
            .context("send agent_info")?;

        // ── Outbound channel + heartbeat task ──
        let (out_tx, mut out_rx) = mpsc::channel::<Message>(OUT_CHANNEL_CAPACITY);
        let hb_tx = out_tx.clone();
        let hb_handle = tokio::spawn(async move {
            // First tick fires immediately — skip it so the first ping
            // happens after the interval, matching the Python agent.
            let mut iv = tokio::time::interval(HEARTBEAT_INTERVAL);
            iv.tick().await;
            loop {
                iv.tick().await;
                let frame = match serde_json::to_string(&Outgoing::Ping) {
                    Ok(s) => s,
                    Err(e) => {
                        warn!(error = %e, "serialize ping failed");
                        continue;
                    }
                };
                if hb_tx.send(Message::Text(frame.into())).await.is_err() {
                    debug!("heartbeat channel closed; exiting heartbeat task");
                    break;
                }
            }
        });

        // ── Message loop ──
        let result: Result<()> = loop {
            tokio::select! {
                msg = read.next() => {
                    match msg {
                        Some(Ok(Message::Text(s))) => {
                            handle_text_frame(
                                &agent_id,
                                s.as_str(),
                                out_tx.clone(),
                                self.shutdown_after_update.clone(),
                                self.token.clone(),
                                self.url.clone(),
                            )
                            .await;
                        }
                        Some(Ok(Message::Binary(_))) => {
                            // Backend currently sends only text frames; log
                            // so protocol drift shows up loudly.
                            warn!("dropped unexpected binary frame from backend");
                        }
                        Some(Ok(Message::Ping(p))) => {
                            // Reply at the WS frame level too — cheap insurance
                            // even though we use application-level pings.
                            let _ = out_tx.send(Message::Pong(p)).await;
                        }
                        Some(Ok(Message::Pong(_))) => {}
                        Some(Ok(Message::Close(frame))) => {
                            info!(?frame, "backend closed ws");
                            break Ok(());
                        }
                        Some(Ok(_)) => {}
                        Some(Err(e)) => break Err(anyhow!("ws read: {e}")),
                        None => break Ok(()),
                    }
                }
                Some(out_msg) = out_rx.recv() => {
                    if let Err(e) = write.send(out_msg).await {
                        break Err(anyhow!("ws write: {e}"));
                    }
                }
                else => break Ok(()),
            }
        };

        hb_handle.abort();
        let _ = write.close().await;
        result
    }
}

async fn handle_text_frame(
    agent_id: &str,
    raw: &str,
    out_tx: mpsc::Sender<Message>,
    shutdown_after_update: Arc<tokio::sync::Notify>,
    token: String,
    ws_url: String,
) {
    // Try the typed envelope first (auth_*/pong/update_available/_).
    match serde_json::from_str::<Incoming>(raw) {
        Ok(Incoming::Pong) => {
            debug!(%agent_id, "heartbeat pong received");
            return;
        }
        Ok(Incoming::AuthOk { .. } | Incoming::AuthError { .. }) => {
            warn!("auth frame received outside handshake; ignoring");
            return;
        }
        Ok(Incoming::UpdateAvailable {
            version,
            download_url,
            sha256,
        }) => {
            info!(
                ?version,
                ?download_url,
                "update_available received; starting self-update"
            );
            let url_opt = download_url.and_then(|u| absolute_url(&u, &ws_url));
            let (Some(url), Some(sha)) = (url_opt, sha256) else {
                warn!("update_available missing download_url or sha256; ignoring");
                return;
            };
            let token = token.clone();
            tokio::spawn(async move {
                let argv: Vec<String> = std::env::args().skip(1).collect();
                match self_update::apply_update(
                    &url,
                    &sha,
                    version.as_deref(),
                    Some(&token),
                    argv,
                    None,
                )
                .await
                {
                    Ok(old) => {
                        info!(old = %old.display(), "self-update applied; signalling shutdown");
                        shutdown_after_update.notify_waiters();
                    }
                    Err(e) => {
                        warn!(error = %e, "self-update failed; staying on current version");
                    }
                }
            });
            return;
        }
        Ok(Incoming::Other) => {
            // Fall through to handler dispatch below.
        }
        Err(e) => {
            let preview: String = raw.chars().take(200).collect();
            warn!(error = %e, %preview, "dropped non-JSON / unparseable frame");
            return;
        }
    }

    // Handler RPC: parse as RequestEnvelope and route.
    let env: RequestEnvelope = match serde_json::from_str(raw) {
        Ok(e) => e,
        Err(e) => {
            let preview: String = raw.chars().take(200).collect();
            warn!(error = %e, %preview, "frame had unknown type but isn't a valid RequestEnvelope");
            return;
        }
    };

    let request_type = env.request_type.clone();
    let request_id = env.request_id.clone();
    let payload = env.payload;
    debug!(
        %request_type,
        request_id = ?request_id,
        "dispatching handler RPC"
    );

    // Spawn the handler so a slow exec doesn't block the WS read loop —
    // each request runs concurrently and pipes its response back via
    // the shared out channel. Matches Python's `_spawn_task` pattern.
    tokio::spawn(async move {
        let response_payload = match handlers::dispatch(&request_type, payload).await {
            Some(v) => v,
            None => {
                warn!(%request_type, "no handler registered; ignoring");
                return;
            }
        };
        let response_type = handlers::response_type_for(&request_type);
        let envelope = Response {
            response_type: &response_type,
            request_id,
            payload: response_payload,
        };
        let json_str = match serde_json::to_string(&envelope) {
            Ok(s) => s,
            Err(e) => {
                warn!(error = %e, %request_type, "serialize response failed");
                return;
            }
        };
        if out_tx.send(Message::Text(json_str.into())).await.is_err() {
            debug!(%request_type, "out channel closed; response dropped");
        }
    });
}

/// Convert an `update_available` `download_url` (which may be
/// path-only, e.g. `/api/v1/.../download`) into an absolute HTTPS URL
/// by borrowing the scheme/host of the WebSocket URL we're already
/// connected to. Mirrors Python's `_absolute_download_url`.
fn absolute_url(maybe_relative: &str, ws_url: &str) -> Option<String> {
    if maybe_relative.starts_with("https://") || maybe_relative.starts_with("http://") {
        return Some(maybe_relative.to_string());
    }
    let base = url::Url::parse(ws_url).ok()?;
    let host = base.host_str()?;
    let scheme = match base.scheme() {
        "wss" => "https",
        "ws" => "http",
        other => other,
    };
    let port_part = match base.port() {
        Some(p) => format!(":{p}"),
        None => String::new(),
    };
    let path = if maybe_relative.starts_with('/') {
        maybe_relative.to_string()
    } else {
        format!("/{maybe_relative}")
    };
    Some(format!("{scheme}://{host}{port_part}{path}"))
}

async fn sleep_with_jitter(ms: u64) {
    let factor = rand::thread_rng().gen_range(-RECONNECT_JITTER_PCT..=RECONNECT_JITTER_PCT);
    let wait = ((ms as f64) * (1.0 + factor)).max(0.0) as u64;
    sleep(Duration::from_millis(wait)).await;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn sleep_with_jitter_terminates() {
        // Just check it returns within a generous bound. ms=10 →
        // worst-case 12 ms.
        let start = std::time::Instant::now();
        sleep_with_jitter(10).await;
        assert!(start.elapsed() < Duration::from_millis(200));
    }
}
