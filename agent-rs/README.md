# mcp-workspace-agent-rs

Rust port of the Python `agent/` (mcp-workspace-agent). Tracking parent task `69d543c8` and subtasks `agent-rs/01..10`.

PoC scope (this commit, task `agent-rs/01`):

- WebSocket connect to the backend agent endpoint
- Auth handshake (`auth` → `auth_ok`)
- `agent_info` push (hostname / host_id / os / shells / agent_version)
- Application-level heartbeat: `{"type":"ping"}` every 30s, expecting `{"type":"pong"}`
- Reconnect with exponential backoff (1s → 60s) + ±20 % jitter
- Graceful shutdown on SIGINT / SIGTERM (Unix) or Ctrl-C (Windows)

Handlers (`exec`, `read_file`, `grep`, PTY, self-update, …) land in subsequent tasks.

## Run

```bash
cargo run -- \
  --url wss://todo.vtech-studios.com/api/v1/workspaces/agent/ws \
  --token ta_xxx
```

Or via env vars (`MCP_AGENT_URL`, `MCP_AGENT_TOKEN`), or via JSON config:

```bash
cat > config.json <<'EOF'
{ "url": "wss://...", "token": "ta_xxx" }
EOF
cargo run -- --config config.json
```

## Verify

After start-up:

1. Backend log shows `Agent connected: ... (<id>)`.
2. `list_remote_agents` MCP tool returns the agent with `is_online=true`,
   `agent_version="0.6.0-dev"`, OS label matching `sys.platform`.
3. Agent log shows `heartbeat pong received` every 30s.
4. Restart the backend → agent reconnects within 60s; `list_remote_agents`
   confirms `is_online` returns true.

## Tests

```bash
cargo test
```

Covers wire-format round-trips and host-id parity with the Python agent
(`sha256(f"{hostname}::{sys.platform}")[:16]`).
