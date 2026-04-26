//! Wire format for the agent ↔ backend WebSocket.
//!
//! The Python agent's frames are the contract; serde shapes here must
//! match what `backend/app/api/v1/endpoints/workspaces/websocket.py`
//! parses. Tests pin the JSON shape so any drift fails locally.
//!
//! PoC scope:
//! - outgoing: `auth`, `agent_info`, `ping`
//! - incoming: `auth_ok`, `auth_error`, `pong`, `update_available`
//!   (any other type → [`Incoming::Other`], handled in subsequent
//!   tasks 03..07)

use serde::{Deserialize, Serialize};

/// Frames the agent sends to the backend. Type-tagged on the `type`
/// field, with payload fields flattened to the top of the JSON object
/// (matches Python's `json.dumps({"type": ..., **fields})` shape).
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Outgoing {
    Auth {
        token: String,
    },
    AgentInfo {
        hostname: String,
        host_id: String,
        /// `win32` / `linux` / `darwin` (matches Python `sys.platform`).
        os: String,
        shells: Vec<String>,
        agent_version: String,
    },
    Ping,
}

/// Frames the agent receives from the backend.
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Incoming {
    AuthOk {
        agent_id: String,
    },
    AuthError {
        #[serde(default)]
        message: Option<String>,
    },
    Pong,
    /// Pushed by the backend when a newer release is available for
    /// this agent's OS/arch/channel. Handled in agent-rs/07.
    UpdateAvailable {
        #[serde(default)]
        version: Option<String>,
        #[serde(default)]
        download_url: Option<String>,
        #[serde(default)]
        sha256: Option<String>,
    },
    /// Anything else (handler RPCs etc.). PoC just logs and skips —
    /// concrete handlers land in 03..07.
    #[serde(other)]
    Other,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn auth_serializes_with_type_field() {
        let s = serde_json::to_string(&Outgoing::Auth { token: "tok".into() }).unwrap();
        let v: serde_json::Value = serde_json::from_str(&s).unwrap();
        assert_eq!(v["type"], "auth");
        assert_eq!(v["token"], "tok");
    }

    #[test]
    fn ping_serializes_to_type_only() {
        let s = serde_json::to_string(&Outgoing::Ping).unwrap();
        assert_eq!(s, r#"{"type":"ping"}"#);
    }

    #[test]
    fn agent_info_fields_at_top_level() {
        let frame = Outgoing::AgentInfo {
            hostname: "h".into(),
            host_id: "abc1234567890def".into(),
            os: "darwin".into(),
            shells: vec!["bash".into(), "zsh".into()],
            agent_version: "0.6.0-dev".into(),
        };
        let v: serde_json::Value = serde_json::to_value(&frame).unwrap();
        assert_eq!(v["type"], "agent_info");
        assert_eq!(v["hostname"], "h");
        assert_eq!(v["host_id"], "abc1234567890def");
        assert_eq!(v["os"], "darwin");
        assert_eq!(v["shells"], json!(["bash", "zsh"]));
        assert_eq!(v["agent_version"], "0.6.0-dev");
    }

    #[test]
    fn auth_ok_deserializes() {
        let v: Incoming =
            serde_json::from_str(r#"{"type":"auth_ok","agent_id":"abc"}"#).unwrap();
        match v {
            Incoming::AuthOk { agent_id } => assert_eq!(agent_id, "abc"),
            other => panic!("expected AuthOk, got {other:?}"),
        }
    }

    #[test]
    fn auth_error_deserializes_with_optional_message() {
        let v: Incoming = serde_json::from_str(r#"{"type":"auth_error"}"#).unwrap();
        assert!(matches!(v, Incoming::AuthError { message: None }));

        let v: Incoming =
            serde_json::from_str(r#"{"type":"auth_error","message":"nope"}"#).unwrap();
        match v {
            Incoming::AuthError { message: Some(m) } => assert_eq!(m, "nope"),
            other => panic!("expected AuthError, got {other:?}"),
        }
    }

    #[test]
    fn pong_deserializes() {
        let v: Incoming = serde_json::from_str(r#"{"type":"pong"}"#).unwrap();
        assert!(matches!(v, Incoming::Pong));
    }

    #[test]
    fn update_available_deserializes_with_partial_fields() {
        let v: Incoming = serde_json::from_str(r#"{"type":"update_available"}"#).unwrap();
        assert!(matches!(
            v,
            Incoming::UpdateAvailable {
                version: None,
                download_url: None,
                sha256: None
            }
        ));
    }

    #[test]
    fn unknown_type_falls_through_to_other() {
        let v: Incoming = serde_json::from_str(
            r#"{"type":"exec","payload":{"cmd":"ls"},"request_id":"r1"}"#,
        )
        .unwrap();
        assert!(matches!(v, Incoming::Other));
    }
}
