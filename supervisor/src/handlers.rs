//! ``supervisor_*`` RPC handlers — the control-plane logic invoked
//! from ``backend.rs`` once a frame's envelope is parsed.
//!
//! One ``Dispatcher`` is constructed per active WS connection: it
//! holds the ``mpsc::Sender`` that ``backend.rs`` drains into the
//! socket, plus the live log subscription (one subscriber per
//! connection — the spec's logs_subscribe is single-shot).
//!
//! Day 3 wires six of the seven RPCs:
//!   - ``supervisor_status``
//!   - ``supervisor_restart``
//!   - ``supervisor_logs``
//!   - ``supervisor_logs_subscribe``
//!   - ``supervisor_logs_unsubscribe``
//!   - ``supervisor_config_reload``
//!
//! ``supervisor_upgrade`` returns a "not yet implemented" response;
//! the full flow with download + sha256 two-stage verify + rollback
//! lands in Day 3.5 alongside ``upgrade.rs``.

use std::path::PathBuf;
use std::sync::Arc;

use parking_lot::{Mutex, RwLock};
use serde::Serialize;
use serde_json::{json, Value};
use tokio::sync::mpsc;
use tokio::task::JoinHandle;
use tokio_tungstenite::tungstenite::Message;
use tracing::warn;

use crate::config::Config;
use crate::process::AgentManager;
use crate::protocol::{
    kind, ConfigReloadResponse, Envelope, LogStream, LogStreamFilter, LogsRequest,
    LogsResponse, RestartRequest, StatusResponse, SupervisorLogPush, UpgradeRequest,
    UpgradeResponse,
};
use crate::upgrade;

struct SubscriptionState {
    handle: JoinHandle<()>,
    ring_id: u64,
}

pub struct Dispatcher {
    agent: Arc<AgentManager>,
    out_tx: mpsc::Sender<Message>,
    config: Arc<RwLock<Config>>,
    config_path: PathBuf,
    subscription: Mutex<Option<SubscriptionState>>,
}

impl Dispatcher {
    pub fn new(
        agent: Arc<AgentManager>,
        out_tx: mpsc::Sender<Message>,
        config: Arc<RwLock<Config>>,
        config_path: PathBuf,
    ) -> Self {
        Self {
            agent,
            out_tx,
            config,
            config_path,
            subscription: Mutex::new(None),
        }
    }

    pub async fn dispatch(&self, env: Envelope<Value>) {
        match env.kind.as_str() {
            kind::SUPERVISOR_STATUS => self.handle_status(env).await,
            kind::SUPERVISOR_RESTART => self.handle_restart(env).await,
            kind::SUPERVISOR_LOGS => self.handle_logs(env).await,
            kind::SUPERVISOR_LOGS_SUBSCRIBE => self.handle_logs_subscribe(env).await,
            kind::SUPERVISOR_LOGS_UNSUBSCRIBE => self.handle_logs_unsubscribe(env).await,
            kind::SUPERVISOR_CONFIG_RELOAD => self.handle_config_reload(env).await,
            kind::SUPERVISOR_UPGRADE => self.handle_upgrade(env).await,
            other => warn!(kind = other, "unknown supervisor frame; ignoring"),
        }
    }

    async fn send_response<T: Serialize>(
        &self,
        request_id: Option<String>,
        kind: &str,
        payload: T,
    ) {
        let env = Envelope {
            kind: kind.to_string(),
            request_id,
            payload,
        };
        match serde_json::to_string(&env) {
            Ok(s) => {
                let _ = self.out_tx.send(Message::Text(s.into())).await;
            }
            Err(e) => warn!(error = %e, kind, "failed to serialize response"),
        }
    }

    async fn handle_status(&self, env: Envelope<Value>) {
        let snap = self.agent.status();
        // Tail the most recent stderr lines (up to 20) for the
        // operator's quick context. Walk the snapshot oldest-first
        // and keep only stderr to preserve order.
        let recent_stderr: Vec<String> = self
            .agent
            .ring()
            .snapshot(Some(200))
            .into_iter()
            .filter(|l| l.stream == LogStream::Stderr)
            .map(|l| l.text)
            .rev()
            .take(20)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect();
        let agent_uptime_s = snap.started_at.map(|s| {
            let now = chrono::Utc::now();
            (now - s).num_seconds().max(0) as u64
        });
        let resp = StatusResponse {
            agent_state: snap.state,
            agent_pid: snap.pid,
            agent_started_at: snap.started_at,
            agent_uptime_s,
            // The agent's own version is reported via its own WS
            // (host_id binding from §2.2). Day 4 will have the backend
            // join the two; for now this is None.
            agent_version: None,
            last_crash_at: snap.last_crash_at,
            last_crash_exit_code: snap.last_crash_exit_code,
            consecutive_crashes: snap.consecutive_crashes,
            recent_stderr,
        };
        self.send_response(env.request_id, kind::SUPERVISOR_STATUS_RESULT, resp)
            .await;
    }

    async fn handle_restart(&self, env: Envelope<Value>) {
        let req: RestartRequest =
            serde_json::from_value(env.payload).unwrap_or_default();
        let resp = self.agent.restart(req.graceful_timeout_ms).await;
        self.send_response(env.request_id, kind::SUPERVISOR_RESTART_RESULT, resp)
            .await;
    }

    async fn handle_logs(&self, env: Envelope<Value>) {
        let req: LogsRequest =
            serde_json::from_value(env.payload).unwrap_or_default();
        let mut lines = self.agent.ring().snapshot(req.lines);
        if let Some(since) = req.since_ts {
            lines.retain(|l| l.ts > since);
        }
        if let Some(filter) = req.stream {
            match filter {
                LogStreamFilter::Stdout => {
                    lines.retain(|l| l.stream == LogStream::Stdout)
                }
                LogStreamFilter::Stderr => {
                    lines.retain(|l| l.stream == LogStream::Stderr)
                }
                LogStreamFilter::Both => {}
            }
        }
        self.send_response(
            env.request_id,
            kind::SUPERVISOR_LOGS_RESULT,
            LogsResponse { lines },
        )
        .await;
    }

    async fn handle_logs_subscribe(&self, env: Envelope<Value>) {
        // Per spec §3.1, subscription is per-connection and replaces
        // any prior one rather than stacking — so drop the existing
        // forwarder before starting a new one.
        if let Some(prev) = self.subscription.lock().take() {
            prev.handle.abort();
            self.agent.ring().unsubscribe(prev.ring_id);
        }

        let mut sub = self.agent.ring().subscribe();
        let ring_id = sub.id;
        let out_tx = self.out_tx.clone();
        let handle = tokio::spawn(async move {
            loop {
                let line = match sub.rx.recv().await {
                    Some(l) => l,
                    None => break, // ring dropped — connection going away
                };
                // Coalesce a burst into one push so each WS frame
                // carries up to 32 lines. Anything still pending after
                // 32 stays for the next iteration.
                let mut batch = vec![line];
                while batch.len() < 32 {
                    match sub.rx.try_recv() {
                        Ok(l) => batch.push(l),
                        Err(_) => break,
                    }
                }
                let env = Envelope {
                    kind: kind::SUPERVISOR_LOG.to_string(),
                    request_id: None,
                    payload: SupervisorLogPush { lines: batch },
                };
                let s = match serde_json::to_string(&env) {
                    Ok(s) => s,
                    Err(_) => break,
                };
                if out_tx.send(Message::Text(s.into())).await.is_err() {
                    break;
                }
            }
        });
        *self.subscription.lock() = Some(SubscriptionState { handle, ring_id });

        self.send_response(
            env.request_id,
            kind::SUPERVISOR_LOGS_SUBSCRIBE_RESULT,
            json!({"success": true}),
        )
        .await;
    }

    async fn handle_logs_unsubscribe(&self, env: Envelope<Value>) {
        if let Some(prev) = self.subscription.lock().take() {
            prev.handle.abort();
            self.agent.ring().unsubscribe(prev.ring_id);
        }
        self.send_response(
            env.request_id,
            kind::SUPERVISOR_LOGS_UNSUBSCRIBE_RESULT,
            json!({"success": true}),
        )
        .await;
    }

    async fn handle_config_reload(&self, env: Envelope<Value>) {
        let new_cfg = match Config::load(&self.config_path) {
            Ok(c) => c,
            Err(e) => {
                return self
                    .send_response(
                        env.request_id,
                        kind::SUPERVISOR_CONFIG_RELOAD_RESULT,
                        ConfigReloadResponse {
                            success: false,
                            errors: vec![format!("{e:#}")],
                            requires_restart: vec![],
                        },
                    )
                    .await;
            }
        };

        let mut requires_restart: Vec<String> = Vec::new();
        {
            let cur = self.config.read();
            if cur.backend.url != new_cfg.backend.url {
                requires_restart.push("backend.url".into());
            }
            if cur.backend.token != new_cfg.backend.token {
                requires_restart.push("backend.token".into());
            }
            if cur.agent != new_cfg.agent {
                requires_restart.push("agent.*".into());
            }
        }

        if !requires_restart.is_empty() {
            // Per spec §8.2, restart-required fields are not
            // hot-applied. The caller must drive ``supervisor_restart``
            // (or the future ``supervisor_reconnect_backend``).
            return self
                .send_response(
                    env.request_id,
                    kind::SUPERVISOR_CONFIG_RELOAD_RESULT,
                    ConfigReloadResponse {
                        success: false,
                        errors: vec![],
                        requires_restart,
                    },
                )
                .await;
        }

        // Hot-reloadable fields land here. The shared config is what
        // the WS reconnect path reads next; fields baked into already
        // running components (LogRing capacity, restart backoffs) are
        // honoured on the next supervisor restart — a known limitation
        // documented in §8.2.
        *self.config.write() = new_cfg;
        self.send_response(
            env.request_id,
            kind::SUPERVISOR_CONFIG_RELOAD_RESULT,
            ConfigReloadResponse {
                success: true,
                errors: vec![],
                requires_restart: vec![],
            },
        )
        .await;
    }

    async fn handle_upgrade(&self, env: Envelope<Value>) {
        let req: UpgradeRequest = match serde_json::from_value(env.payload) {
            Ok(r) => r,
            Err(e) => {
                return self
                    .send_response(
                        env.request_id,
                        kind::SUPERVISOR_UPGRADE_RESULT,
                        UpgradeResponse {
                            success: false,
                            new_version: None,
                            error: Some(format!("bad upgrade request: {e}")),
                        },
                    )
                    .await;
            }
        };
        let target_path = self.config.read().agent.upgrade_target_path.clone();
        let target = match target_path {
            Some(p) => p,
            None => {
                return self
                    .send_response(
                        env.request_id,
                        kind::SUPERVISOR_UPGRADE_RESULT,
                        UpgradeResponse {
                            success: false,
                            new_version: None,
                            error: Some(
                                "no upgrade target configured (agent.upgrade_target_path is unset; \
                                 expected for uv-run mode)"
                                    .into(),
                            ),
                        },
                    )
                    .await;
            }
        };
        let resp = upgrade::run_upgrade(
            &target,
            &req.download_url,
            &req.sha256,
            self.agent.clone(),
        )
        .await;
        self.send_response(env.request_id, kind::SUPERVISOR_UPGRADE_RESULT, resp)
            .await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::path::PathBuf;

    use crate::config::{
        AgentConfig, AgentMode, BackendConfig, LogConfig, RestartConfig, SupervisorLogConfig,
    };
    use crate::log_capture::LogRing;
    use crate::process::{AgentCommand, AgentManager, NoShutdownHook};

    fn dummy_agent() -> Arc<AgentManager> {
        let cmd = AgentCommand {
            program: PathBuf::from(if cfg!(windows) { "cmd.exe" } else { "/bin/sh" }),
            args: vec!["-c".into(), "true".into()],
            cwd: std::env::current_dir().unwrap(),
            env: HashMap::new(),
        };
        let restart = RestartConfig {
            backoff_initial_ms: 10,
            backoff_max_ms: 40,
            backoff_jitter_pct: 0,
            graceful_timeout_ms: 100,
        };
        let ring = LogRing::new(100, 4096, 16);
        Arc::new(
            AgentManager::new(cmd, restart, ring, Arc::new(NoShutdownHook))
                .expect("AgentManager"),
        )
    }

    fn dummy_config() -> Config {
        Config {
            backend: BackendConfig {
                url: "wss://example/sup/ws".into(),
                token: "sv_dummy".into(),
                heartbeat_interval_s: 30,
            },
            agent: AgentConfig {
                mode: AgentMode::UvRun,
                cwd: std::env::current_dir().unwrap(),
                url: "wss://example/agent/ws".into(),
                token: "ta_dummy".into(),
                exec_path: None,
                upgrade_target_path: None,
                managed_dir: None,
                update_channel: "stable".into(),
                update_check_interval_s: 3600,
            },
            log: LogConfig::default(),
            restart: RestartConfig::default(),
            supervisor_log: SupervisorLogConfig::default(),
        }
    }

    fn make_dispatcher() -> (Dispatcher, mpsc::Receiver<Message>, Arc<AgentManager>) {
        let agent = dummy_agent();
        let (tx, rx) = mpsc::channel::<Message>(16);
        let cfg = Arc::new(RwLock::new(dummy_config()));
        let dispatcher = Dispatcher::new(
            agent.clone(),
            tx,
            cfg,
            PathBuf::from("/nonexistent/config.toml"),
        );
        (dispatcher, rx, agent)
    }

    fn parse_text(msg: Message) -> Value {
        match msg {
            Message::Text(s) => serde_json::from_str(s.as_str()).expect("valid JSON"),
            other => panic!("expected Text frame, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn handle_status_returns_status_result_envelope() {
        let (dispatcher, mut rx, _agent) = make_dispatcher();
        dispatcher
            .dispatch(Envelope {
                kind: kind::SUPERVISOR_STATUS.into(),
                request_id: Some("req-status".into()),
                payload: json!({}),
            })
            .await;
        let frame = rx.recv().await.expect("response");
        let v = parse_text(frame);
        assert_eq!(v["type"], kind::SUPERVISOR_STATUS_RESULT);
        assert_eq!(v["request_id"], "req-status");
        // No agent has spawned, so state is the Default ("stopped").
        assert_eq!(v["payload"]["agent_state"], "stopped");
        assert_eq!(v["payload"]["consecutive_crashes"], 0);
    }

    #[tokio::test]
    async fn handle_logs_returns_buffered_lines() {
        let (dispatcher, mut rx, agent) = make_dispatcher();
        agent.ring().push(LogStream::Stdout, "first");
        agent.ring().push(LogStream::Stderr, "boom");
        agent.ring().push(LogStream::Stdout, "second");

        dispatcher
            .dispatch(Envelope {
                kind: kind::SUPERVISOR_LOGS.into(),
                request_id: Some("req-logs".into()),
                payload: json!({"lines": 10, "stream": "stderr"}),
            })
            .await;
        let v = parse_text(rx.recv().await.expect("response"));
        assert_eq!(v["type"], kind::SUPERVISOR_LOGS_RESULT);
        let lines = v["payload"]["lines"].as_array().expect("lines array");
        assert_eq!(lines.len(), 1);
        assert_eq!(lines[0]["text"], "boom");
        assert_eq!(lines[0]["stream"], "stderr");
    }

    #[tokio::test]
    async fn handle_logs_unsubscribe_is_idempotent() {
        let (dispatcher, mut rx, _agent) = make_dispatcher();
        // No prior subscription — must not panic.
        dispatcher
            .dispatch(Envelope {
                kind: kind::SUPERVISOR_LOGS_UNSUBSCRIBE.into(),
                request_id: Some("u1".into()),
                payload: json!({}),
            })
            .await;
        let v = parse_text(rx.recv().await.expect("response"));
        assert_eq!(v["type"], kind::SUPERVISOR_LOGS_UNSUBSCRIBE_RESULT);
        assert_eq!(v["payload"]["success"], true);
    }

    #[tokio::test]
    async fn handle_logs_subscribe_then_unsubscribe_replaces_subscription() {
        let (dispatcher, mut rx, agent) = make_dispatcher();
        // First subscribe.
        dispatcher
            .dispatch(Envelope {
                kind: kind::SUPERVISOR_LOGS_SUBSCRIBE.into(),
                request_id: Some("s1".into()),
                payload: json!({}),
            })
            .await;
        let first = parse_text(rx.recv().await.expect("subscribe ack"));
        assert_eq!(first["type"], kind::SUPERVISOR_LOGS_SUBSCRIBE_RESULT);

        // A push lands on the live subscriber and is forwarded as a
        // supervisor_log push frame.
        agent.ring().push(LogStream::Stdout, "live-line");
        // Receive frames until we see a supervisor_log push (the
        // forwarder may briefly batch / coalesce).
        let mut saw_push = false;
        for _ in 0..5 {
            let f = tokio::time::timeout(
                std::time::Duration::from_millis(500),
                rx.recv(),
            )
            .await;
            match f {
                Ok(Some(msg)) => {
                    let v = parse_text(msg);
                    if v["type"] == kind::SUPERVISOR_LOG {
                        let lines = v["payload"]["lines"].as_array().unwrap();
                        assert!(lines.iter().any(|l| l["text"] == "live-line"));
                        saw_push = true;
                        break;
                    }
                }
                _ => break,
            }
        }
        assert!(saw_push, "expected a supervisor_log push frame");

        // Unsubscribe stops the forwarder.
        dispatcher
            .dispatch(Envelope {
                kind: kind::SUPERVISOR_LOGS_UNSUBSCRIBE.into(),
                request_id: Some("u1".into()),
                payload: json!({}),
            })
            .await;
        let _ack = rx.recv().await.expect("unsubscribe ack");
    }
}
