//! TOML-backed configuration with validation.
//!
//! The shape mirrors ``supervisor/config.example.toml``. Hot reload
//! is implemented at a higher layer (``backend.rs``); this module is
//! pure parsing + validation. Loading never panics on missing
//! optional fields — defaults are explicit so ``cargo build`` of a
//! brand-new install works without hand-editing every key.

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Config {
    pub backend: BackendConfig,
    pub agent: AgentConfig,
    #[serde(default)]
    pub log: LogConfig,
    #[serde(default)]
    pub restart: RestartConfig,
    #[serde(default)]
    pub supervisor_log: SupervisorLogConfig,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct BackendConfig {
    pub url: String,
    pub token: String,
    #[serde(default = "default_heartbeat_interval_s")]
    pub heartbeat_interval_s: u32,
}

fn default_heartbeat_interval_s() -> u32 {
    30
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AgentConfig {
    pub mode: AgentMode,
    pub cwd: PathBuf,
    pub url: String,
    pub token: String,
    /// Path to a packaged agent executable. **Required when
    /// `mode = "exec"`**, ignored for `mode = "uv-run"` (which always
    /// invokes `uv run python main.py` from `cwd`) and for
    /// `mode = "managed"` (where the supervisor controls the path).
    #[serde(default)]
    pub exec_path: Option<PathBuf>,
    /// Optional path to the single agent binary that
    /// ``supervisor_upgrade`` swaps. Unset for uv-run deployments
    /// (which have no single binary to replace) — the upgrade RPC
    /// then returns a "no upgrade target configured" error instead
    /// of touching anything on disk.
    #[serde(default)]
    pub upgrade_target_path: Option<PathBuf>,
    /// Directory where the supervisor stores + manages the agent
    /// binary in `mode = "managed"`. Defaults to
    /// `%LOCALAPPDATA%\mcp-workspace\agent\` on Windows. Ignored for
    /// uv-run / exec modes (operator owns the binary).
    #[serde(default)]
    pub managed_dir: Option<PathBuf>,
    /// Backend release channel polled in `managed` mode. Ignored for
    /// uv-run / exec modes.
    #[serde(default = "default_update_channel")]
    pub update_channel: String,
    /// How often the supervisor checks the backend for a newer agent
    /// release in `managed` mode (seconds, 0 disables periodic
    /// checks; bootstrap-time download still happens).
    #[serde(default = "default_update_check_interval_s")]
    pub update_check_interval_s: u32,
}

fn default_update_channel() -> String {
    "stable".to_string()
}

fn default_update_check_interval_s() -> u32 {
    3600
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "kebab-case")]
pub enum AgentMode {
    /// ``uv run python main.py --url <url> --token <token>`` from ``cwd``.
    UvRun,
    /// Run a packaged agent binary directly:
    /// ``<exec_path> --url <url> --token <token>`` from ``cwd``.
    /// Used for hosts that only have the PyInstaller-packaged exe
    /// (no Python source / uv install) or for the future Rust agent.
    Exec,
    /// Supervisor-only deployment: supervisor downloads + manages the
    /// agent binary at `managed_dir`, polls `update_channel` for
    /// updates, and spawns the agent with the token from `agent.token`.
    /// Operators don't need to handle the agent binary at all — the
    /// `--bootstrap <install_token>` flow installs everything.
    Managed,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct LogConfig {
    pub ring_capacity: usize,
    pub file_path: String,
    pub max_line_bytes: usize,
    pub subscriber_channel_capacity: usize,
}

impl Default for LogConfig {
    fn default() -> Self {
        Self {
            ring_capacity: 10_000,
            file_path: String::new(),
            max_line_bytes: 4096,
            subscriber_channel_capacity: 256,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RestartConfig {
    pub backoff_initial_ms: u64,
    pub backoff_max_ms: u64,
    pub backoff_jitter_pct: u8,
    pub graceful_timeout_ms: u64,
}

impl Default for RestartConfig {
    fn default() -> Self {
        Self {
            backoff_initial_ms: 1_000,
            backoff_max_ms: 32_000,
            backoff_jitter_pct: 20,
            graceful_timeout_ms: 5_000,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SupervisorLogConfig {
    pub dir: String,
    pub rotation_size_mb: u32,
    pub rotation_keep: u32,
}

impl Default for SupervisorLogConfig {
    fn default() -> Self {
        Self {
            dir: String::new(),
            rotation_size_mb: 10,
            rotation_keep: 5,
        }
    }
}

impl Config {
    pub fn load(path: &Path) -> Result<Self> {
        let raw = std::fs::read_to_string(path)
            .with_context(|| format!("failed to read config file at {}", path.display()))?;
        let cfg: Self = toml::from_str(&raw)
            .with_context(|| format!("failed to parse TOML at {}", path.display()))?;
        cfg.validate()?;
        Ok(cfg)
    }

    pub fn validate(&self) -> Result<()> {
        // Tokens & URLs.
        if !self.backend.token.starts_with("sv_") {
            return Err(anyhow!(
                "backend.token must start with 'sv_' (got {:?})",
                redact(&self.backend.token)
            ));
        }
        if !self.agent.token.starts_with("ta_") {
            return Err(anyhow!(
                "agent.token must start with 'ta_' (got {:?})",
                redact(&self.agent.token)
            ));
        }
        for (label, url) in [("backend.url", &self.backend.url), ("agent.url", &self.agent.url)] {
            let parsed = url::Url::parse(url)
                .with_context(|| format!("{label} is not a valid URL: {url}"))?;
            match parsed.scheme() {
                "ws" | "wss" => {}
                other => {
                    return Err(anyhow!("{label} must be ws:// or wss:// (got {other})"));
                }
            }
        }

        // Agent cwd must exist (validating early gives a nicer error than
        // ``CreateProcess`` returning a cryptic ERROR_FILE_NOT_FOUND).
        if !self.agent.cwd.is_dir() {
            return Err(anyhow!(
                "agent.cwd is not a directory: {}",
                self.agent.cwd.display()
            ));
        }

        // Mode-specific requirements.
        match self.agent.mode {
            AgentMode::UvRun => {
                // uv-run reads main.py from cwd; no extra file checks
                // (we leave that to uv to surface as a runtime error).
            }
            AgentMode::Exec => {
                let exec_path = self.agent.exec_path.as_ref().ok_or_else(|| {
                    anyhow!("agent.exec_path is required when mode = \"exec\"")
                })?;
                if !exec_path.is_file() {
                    return Err(anyhow!(
                        "agent.exec_path is not a file: {}",
                        exec_path.display()
                    ));
                }
            }
            AgentMode::Managed => {
                // managed_dir is optional in config (we have a sensible
                // platform-specific default); the supervisor itself
                // ensures the directory exists at startup before
                // spawning the agent. Don't enforce existence here —
                // the bootstrap flow may be writing the config before
                // the dir is created.
            }
        }

        // Numeric guards.
        if self.log.ring_capacity == 0 {
            return Err(anyhow!("log.ring_capacity must be > 0"));
        }
        if self.log.max_line_bytes < 64 {
            return Err(anyhow!("log.max_line_bytes must be >= 64"));
        }
        if self.log.subscriber_channel_capacity == 0 {
            return Err(anyhow!("log.subscriber_channel_capacity must be > 0"));
        }
        if self.restart.backoff_initial_ms == 0
            || self.restart.backoff_max_ms < self.restart.backoff_initial_ms
        {
            return Err(anyhow!(
                "restart.backoff_initial_ms must be > 0 and <= backoff_max_ms"
            ));
        }
        if self.restart.backoff_jitter_pct > 100 {
            return Err(anyhow!("restart.backoff_jitter_pct must be in 0..=100"));
        }
        if self.backend.heartbeat_interval_s == 0 {
            return Err(anyhow!("backend.heartbeat_interval_s must be > 0"));
        }
        Ok(())
    }
}

/// Mask a token for logs / error messages — keep just the prefix.
pub(crate) fn redact(token: &str) -> String {
    if token.len() <= 4 {
        return "<short>".to_string();
    }
    format!("{}...", &token[..token.len().min(4)])
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::TempDir;

    fn write_config(dir: &TempDir, body: &str) -> PathBuf {
        let p = dir.path().join("config.toml");
        let mut f = std::fs::File::create(&p).unwrap();
        f.write_all(body.as_bytes()).unwrap();
        p
    }

    #[test]
    fn parses_minimal_config() {
        let tmp = TempDir::new().unwrap();
        let agent_cwd = tmp.path().join("agent");
        std::fs::create_dir(&agent_cwd).unwrap();
        let p = write_config(
            &tmp,
            &format!(
                r#"
[backend]
url = "wss://example.com/sup/ws"
token = "sv_aabbccdd"

[agent]
mode = "uv-run"
cwd = "{}"
url = "wss://example.com/agent/ws"
token = "ta_eeff0011"
"#,
                agent_cwd.display().to_string().replace('\\', "/"),
            ),
        );
        let cfg = Config::load(&p).expect("load config");
        assert_eq!(cfg.backend.url, "wss://example.com/sup/ws");
        assert_eq!(cfg.backend.heartbeat_interval_s, 30);
        assert_eq!(cfg.log.ring_capacity, 10_000);
        assert!(matches!(cfg.agent.mode, AgentMode::UvRun));
    }

    #[test]
    fn rejects_wrong_token_prefix() {
        let tmp = TempDir::new().unwrap();
        let agent_cwd = tmp.path().join("agent");
        std::fs::create_dir(&agent_cwd).unwrap();
        let p = write_config(
            &tmp,
            &format!(
                r#"
[backend]
url = "wss://example.com/sup/ws"
token = "wrong_prefix"

[agent]
mode = "uv-run"
cwd = "{}"
url = "wss://example.com/agent/ws"
token = "ta_xx"
"#,
                agent_cwd.display().to_string().replace('\\', "/"),
            ),
        );
        let err = Config::load(&p).unwrap_err();
        assert!(err.to_string().contains("backend.token must start with 'sv_'"));
    }

    #[test]
    fn rejects_missing_agent_cwd() {
        let tmp = TempDir::new().unwrap();
        let p = write_config(
            &tmp,
            r#"
[backend]
url = "wss://example.com/sup/ws"
token = "sv_aa"

[agent]
mode = "uv-run"
cwd = "/definitely/does/not/exist/abcxyz"
url = "wss://example.com/agent/ws"
token = "ta_bb"
"#,
        );
        let err = Config::load(&p).unwrap_err();
        assert!(err.to_string().contains("agent.cwd is not a directory"));
    }

    #[test]
    fn exec_mode_parses_with_exec_path() {
        let tmp = TempDir::new().unwrap();
        let agent_cwd = tmp.path().join("agent");
        std::fs::create_dir(&agent_cwd).unwrap();
        let exec_path = agent_cwd.join("mcp-workspace-agent.exe");
        std::fs::File::create(&exec_path).unwrap();
        let p = write_config(
            &tmp,
            &format!(
                r#"
[backend]
url = "wss://example.com/sup/ws"
token = "sv_aabbccdd"

[agent]
mode = "exec"
exec_path = "{}"
cwd = "{}"
url = "wss://example.com/agent/ws"
token = "ta_eeff0011"
"#,
                exec_path.display().to_string().replace('\\', "/"),
                agent_cwd.display().to_string().replace('\\', "/"),
            ),
        );
        let cfg = Config::load(&p).expect("load exec-mode config");
        assert!(matches!(cfg.agent.mode, AgentMode::Exec));
        assert_eq!(cfg.agent.exec_path.as_ref().unwrap(), &exec_path);
    }

    #[test]
    fn exec_mode_requires_exec_path() {
        let tmp = TempDir::new().unwrap();
        let agent_cwd = tmp.path().join("agent");
        std::fs::create_dir(&agent_cwd).unwrap();
        let p = write_config(
            &tmp,
            &format!(
                r#"
[backend]
url = "wss://example.com/sup/ws"
token = "sv_aabbccdd"

[agent]
mode = "exec"
cwd = "{}"
url = "wss://example.com/agent/ws"
token = "ta_eeff0011"
"#,
                agent_cwd.display().to_string().replace('\\', "/"),
            ),
        );
        let err = Config::load(&p).unwrap_err();
        assert!(err.to_string().contains("agent.exec_path is required"));
    }

    #[test]
    fn exec_mode_rejects_missing_exec_path_file() {
        let tmp = TempDir::new().unwrap();
        let agent_cwd = tmp.path().join("agent");
        std::fs::create_dir(&agent_cwd).unwrap();
        let p = write_config(
            &tmp,
            &format!(
                r#"
[backend]
url = "wss://example.com/sup/ws"
token = "sv_aabbccdd"

[agent]
mode = "exec"
exec_path = "{}/does-not-exist.exe"
cwd = "{}"
url = "wss://example.com/agent/ws"
token = "ta_eeff0011"
"#,
                agent_cwd.display().to_string().replace('\\', "/"),
                agent_cwd.display().to_string().replace('\\', "/"),
            ),
        );
        let err = Config::load(&p).unwrap_err();
        assert!(err.to_string().contains("agent.exec_path is not a file"));
    }

    #[test]
    fn rejects_non_ws_scheme() {
        let tmp = TempDir::new().unwrap();
        let agent_cwd = tmp.path().join("agent");
        std::fs::create_dir(&agent_cwd).unwrap();
        let p = write_config(
            &tmp,
            &format!(
                r#"
[backend]
url = "https://example.com/sup/ws"
token = "sv_aa"

[agent]
mode = "uv-run"
cwd = "{}"
url = "wss://example.com/agent/ws"
token = "ta_bb"
"#,
                agent_cwd.display().to_string().replace('\\', "/"),
            ),
        );
        let err = Config::load(&p).unwrap_err();
        assert!(err.to_string().contains("backend.url must be ws://"));
    }
}
