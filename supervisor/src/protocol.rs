//! Wire format for the supervisor ↔ backend WebSocket.
//!
//! Envelope shape: ``{type, request_id?, payload}`` — same as the
//! agent envelope so the backend dispatch path can be uniform, but
//! the type namespace is disjoint (``supervisor_*`` vs ``terminal_*``
//! / ``exec_*`` etc.).
//!
//! The JSON shape is the **only** contract; backend can be evolved
//! independently as long as fields don't disappear without bumping
//! the supervisor version.

use serde::{Deserialize, Serialize};

/// Top-level envelope. ``request_id`` is present on RPC pairs and
/// absent on push frames.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Envelope<P> {
    #[serde(rename = "type")]
    pub kind: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    pub payload: P,
}

/// First WS frame after the connection is open. The backend rejects
/// anything else with code 4008.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AuthFrame {
    #[serde(rename = "type")]
    pub kind: AuthKind,
    pub token: String,
    pub host_id: String,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AuthKind {
    Auth,
}

/// Push: supervisor announces itself + agent state on connect and on
/// every state transition.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SupervisorInfo {
    pub hostname: String,
    pub host_id: String,
    pub os: String,
    pub supervisor_version: String,
    pub agent_version: Option<String>,
    pub agent_pid: Option<u32>,
    pub agent_uptime_s: Option<u64>,
}

/// Push: discrete agent lifecycle events.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SupervisorEvent {
    pub event: SupervisorEventKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub consecutive_crashes: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exit_code: Option<i32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SupervisorEventKind {
    AgentStarted,
    AgentRestarted,
    AgentCrashed,
    UpgradeStarted,
    UpgradeCompleted,
    UpgradeFailed,
}

/// Single captured log line.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct LogLine {
    pub ts: chrono::DateTime<chrono::Utc>,
    pub stream: LogStream,
    pub text: String,
    #[serde(default)]
    pub truncated: bool,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum LogStream {
    Stdout,
    Stderr,
}

/// Push (during an active subscription only): live tail.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SupervisorLogPush {
    pub lines: Vec<LogLine>,
}

// ── RPC requests ────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct StatusRequest {}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RestartRequest {
    #[serde(default)]
    pub graceful_timeout_ms: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct LogsRequest {
    #[serde(default)]
    pub lines: Option<usize>,
    #[serde(default)]
    pub since_ts: Option<chrono::DateTime<chrono::Utc>>,
    #[serde(default)]
    pub stream: Option<LogStreamFilter>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum LogStreamFilter {
    Stdout,
    Stderr,
    Both,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct UpgradeRequest {
    pub download_url: String,
    pub sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct ConfigReloadRequest {}

// ── RPC responses ───────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct StatusResponse {
    pub agent_state: AgentState,
    pub agent_pid: Option<u32>,
    pub agent_started_at: Option<chrono::DateTime<chrono::Utc>>,
    pub agent_uptime_s: Option<u64>,
    pub agent_version: Option<String>,
    pub last_crash_at: Option<chrono::DateTime<chrono::Utc>>,
    pub last_crash_exit_code: Option<i32>,
    pub consecutive_crashes: u32,
    pub recent_stderr: Vec<String>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AgentState {
    Stopped,
    Starting,
    Running,
    Stopping,
    Crashed,
}

impl Default for AgentState {
    fn default() -> Self {
        AgentState::Stopped
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RestartResponse {
    pub restarted: bool,
    pub new_pid: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct LogsResponse {
    pub lines: Vec<LogLine>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct UpgradeResponse {
    pub success: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub new_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ConfigReloadResponse {
    pub success: bool,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub errors: Vec<String>,
    /// Fields that were attempted to be hot-reloaded but require a
    /// full restart instead. Empty when the reload was clean.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub requires_restart: Vec<String>,
}

// ── Frame type names ────────────────────────────────────────────
//
// Centralised so handlers and tests can refer to a single source.

pub mod kind {
    pub const AUTH: &str = "auth";
    pub const AUTH_OK: &str = "auth_ok";

    // Push (supervisor → backend)
    pub const SUPERVISOR_INFO: &str = "supervisor_info";
    pub const SUPERVISOR_EVENT: &str = "supervisor_event";
    pub const SUPERVISOR_LOG: &str = "supervisor_log";

    // RPC (backend → supervisor)
    pub const SUPERVISOR_STATUS: &str = "supervisor_status";
    pub const SUPERVISOR_RESTART: &str = "supervisor_restart";
    pub const SUPERVISOR_LOGS: &str = "supervisor_logs";
    pub const SUPERVISOR_LOGS_SUBSCRIBE: &str = "supervisor_logs_subscribe";
    pub const SUPERVISOR_LOGS_UNSUBSCRIBE: &str = "supervisor_logs_unsubscribe";
    pub const SUPERVISOR_UPGRADE: &str = "supervisor_upgrade";
    pub const SUPERVISOR_CONFIG_RELOAD: &str = "supervisor_config_reload";

    // RPC responses (supervisor → backend)
    pub const SUPERVISOR_STATUS_RESULT: &str = "supervisor_status_result";
    pub const SUPERVISOR_RESTART_RESULT: &str = "supervisor_restart_result";
    pub const SUPERVISOR_LOGS_RESULT: &str = "supervisor_logs_result";
    pub const SUPERVISOR_LOGS_SUBSCRIBE_RESULT: &str = "supervisor_logs_subscribe_result";
    pub const SUPERVISOR_LOGS_UNSUBSCRIBE_RESULT: &str = "supervisor_logs_unsubscribe_result";
    pub const SUPERVISOR_UPGRADE_RESULT: &str = "supervisor_upgrade_result";
    pub const SUPERVISOR_CONFIG_RELOAD_RESULT: &str = "supervisor_config_reload_result";
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn auth_frame_round_trip() {
        let af = AuthFrame {
            kind: AuthKind::Auth,
            token: "sv_aabb".into(),
            host_id: "h0".into(),
        };
        let s = serde_json::to_string(&af).unwrap();
        // The ``type`` field uses snake_case via the enum, not the
        // struct field name.
        assert!(s.contains(r#""type":"auth""#));
        let back: AuthFrame = serde_json::from_str(&s).unwrap();
        assert_eq!(af, back);
    }

    #[test]
    fn envelope_with_request_id() {
        let e = Envelope {
            kind: kind::SUPERVISOR_STATUS.into(),
            request_id: Some("req-1".into()),
            payload: StatusRequest::default(),
        };
        let v: serde_json::Value = serde_json::to_value(&e).unwrap();
        assert_eq!(v["type"], "supervisor_status");
        assert_eq!(v["request_id"], "req-1");
        assert_eq!(v["payload"], json!({}));
    }

    #[test]
    fn envelope_without_request_id_omits_field() {
        let e = Envelope {
            kind: kind::SUPERVISOR_LOG.into(),
            request_id: None,
            payload: SupervisorLogPush { lines: vec![] },
        };
        let s = serde_json::to_string(&e).unwrap();
        assert!(!s.contains("request_id"));
    }

    #[test]
    fn supervisor_event_kind_serializes_snake_case() {
        let ev = SupervisorEvent {
            event: SupervisorEventKind::AgentCrashed,
            consecutive_crashes: Some(3),
            exit_code: Some(-1),
            message: None,
        };
        let v: serde_json::Value = serde_json::to_value(&ev).unwrap();
        assert_eq!(v["event"], "agent_crashed");
        assert_eq!(v["consecutive_crashes"], 3);
        assert!(v.get("message").is_none() || v["message"].is_null());
    }

    #[test]
    fn agent_state_serializes_snake_case() {
        let s = serde_json::to_string(&AgentState::Running).unwrap();
        assert_eq!(s, "\"running\"");
        let back: AgentState = serde_json::from_str(&s).unwrap();
        assert_eq!(back, AgentState::Running);
    }
}
