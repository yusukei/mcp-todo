//! Request/response handlers (`exec` / `read_file` / `write_file` / ...).
//!
//! Each submodule implements one or more `async fn handle_xxx(payload)
//! -> serde_json::Value` functions. The dispatcher in [`dispatch`]
//! looks up the handler by `request_type` and returns the inner JSON
//! value; the surrounding envelope (`{type, request_id, payload}`) is
//! built by the WS client. This mirrors the Python split between
//! `_HANDLERS` (handler map) and `_run_handler` (envelope wrapping)
//! at `agent/main.py:2619`.
//!
//! Handlers never panic on bad input — they return an `{"error": "..."}`
//! object instead. The envelope still goes back to the backend with
//! the corresponding `<request_type>_result` (or legacy alias from
//! [`response_type_for`]), letting the MCP client surface a structured
//! error to the caller.

use serde_json::{json, Value};

pub mod constants;
pub mod exec;
pub mod fs_read;
pub mod fs_write;

/// Map a handler request type (the inbound `type`) to the response
/// envelope type. Mirrors `_RESPONSE_TYPE_FOR` in `agent/main.py:1724`.
/// Unknown types fall through to `<type>_result`.
pub fn response_type_for(request_type: &str) -> String {
    match request_type {
        "exec" => "exec_result".into(),
        "exec_background" => "exec_background_result".into(),
        "exec_status" => "exec_status_result".into(),
        "read_file" => "file_content".into(),
        "write_file" => "write_result".into(),
        "edit_file" => "edit_result".into(),
        "list_dir" => "dir_listing".into(),
        "tree" => "tree_result".into(),
        "stat" => "stat_result".into(),
        "mkdir" => "mkdir_result".into(),
        "delete" => "delete_result".into(),
        "move" => "move_result".into(),
        "copy" => "copy_result".into(),
        "glob" => "glob_result".into(),
        "grep" => "grep_result".into(),
        other => format!("{other}_result"),
    }
}

/// Run the handler for `request_type` and return the inner payload
/// JSON value. Returns `None` if no handler is registered (the caller
/// can decide whether to log + drop or surface an error envelope).
pub async fn dispatch(request_type: &str, payload: Value) -> Option<Value> {
    match request_type {
        "exec" => Some(exec::handle_exec(payload).await),
        "read_file" => Some(fs_read::handle_read_file(payload).await),
        "write_file" => Some(fs_write::handle_write_file(payload).await),
        "list_dir" => Some(fs_read::handle_list_dir(payload).await),
        "stat" => Some(fs_read::handle_stat(payload).await),
        "mkdir" => Some(fs_write::handle_mkdir(payload).await),
        "delete" => Some(fs_write::handle_delete(payload).await),
        "move" => Some(fs_write::handle_move(payload).await),
        "copy" => Some(fs_write::handle_copy(payload).await),
        "glob" => Some(fs_read::handle_glob(payload).await),
        _ => None,
    }
}

/// Standard `{"error": "<msg>"}` payload — used by every handler so
/// the MCP layer sees a uniform error shape regardless of which
/// handler failed.
pub fn error_payload(msg: impl Into<String>) -> Value {
    json!({ "error": msg.into() })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn response_type_aliases_match_python() {
        // Pin the legacy aliases — these names are part of the wire
        // protocol and changing them silently would break MCP clients.
        assert_eq!(response_type_for("read_file"), "file_content");
        assert_eq!(response_type_for("list_dir"), "dir_listing");
        assert_eq!(response_type_for("write_file"), "write_result");
        assert_eq!(response_type_for("exec"), "exec_result");
        assert_eq!(response_type_for("stat"), "stat_result");
    }

    #[test]
    fn unknown_type_gets_result_suffix() {
        assert_eq!(response_type_for("hypothetical"), "hypothetical_result");
    }

    #[tokio::test]
    async fn dispatch_unknown_type_returns_none() {
        let r = dispatch("totally_unknown", json!({})).await;
        assert!(r.is_none());
    }
}
