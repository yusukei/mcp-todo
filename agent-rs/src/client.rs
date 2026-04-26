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
//! 5. Read frames in a loop; for now we only react to `pong` /
//!    `auth_*`. Other frames are logged and dropped (handler dispatch
//!    lands in subsequent tasks).
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

use crate::proto::{Incoming, Outgoing};
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
}

impl Client {
    pub fn new(url: String, token: String) -> Self {
        Self { url, token }
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
                            handle_text_frame(&agent_id, s.as_str());
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

fn handle_text_frame(agent_id: &str, raw: &str) {
    let parsed: Incoming = match serde_json::from_str(raw) {
        Ok(v) => v,
        Err(e) => {
            // Loud, not silent — protocol drift is information operators need.
            let preview: String = raw.chars().take(200).collect();
            warn!(error = %e, %preview, "dropped non-JSON / unparseable frame");
            return;
        }
    };
    match parsed {
        Incoming::Pong => {
            debug!(%agent_id, "heartbeat pong received");
        }
        Incoming::AuthOk { .. } | Incoming::AuthError { .. } => {
            warn!("auth frame received outside handshake; ignoring");
        }
        Incoming::UpdateAvailable {
            version,
            download_url,
            ..
        } => {
            info!(
                ?version,
                ?download_url,
                "update_available received (self-update lands in agent-rs/07)"
            );
        }
        Incoming::Other => {
            // Handlers (exec / read_file / ...) will land in 03..07.
            // Until then, log so we know something arrived.
            debug!("received non-PoC frame; will be handled in later tasks");
        }
    }
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
