//! `--bootstrap <install_token>` install flow.
//!
//! Called once per machine via the install PowerShell:
//!
//! 1. POST `/api/v1/workspaces/supervisors/exchange` with the
//!    install token → receive `sv_*` + `ta_*` tokens, supervisor /
//!    agent IDs, and the WS URLs.
//! 2. Compute conventional install paths (per-user, no admin needed).
//! 3. Write `config.toml` (mode=managed, includes both tokens).
//! 4. Download the latest stable agent binary into `managed_dir`.
//! 5. Register a Scheduled Task on Windows so the supervisor starts
//!    on user logon (best-effort; logged + skipped on POSIX).
//! 6. Spawn the supervisor in the background, detached from this
//!    console (the install PowerShell exits immediately after).
//! 7. Print `OK install complete: …` to stdout for the install
//!    PowerShell to grep.

use std::path::{Path, PathBuf};
use std::process::Stdio;

use anyhow::{anyhow, bail, Context, Result};
use serde::Deserialize;
use tokio::fs;
use tokio::process::Command as AsyncCommand;
use tracing::{info, warn};

use crate::agent_release::{
    current_platform, install_latest, supervisor_config_dir, supervisor_install_dir,
};
use crate::process::{default_managed_agent_exe_name, managed_agent_dir};
use crate::config::{AgentConfig, AgentMode};

/// JSON shape returned by `POST /api/v1/workspaces/supervisors/exchange`.
#[derive(Debug, Clone, Deserialize)]
pub struct ExchangeResponse {
    pub supervisor_id: String,
    pub supervisor_token: String,
    pub supervisor_name: String,
    pub agent_id: String,
    pub agent_token: String,
    pub agent_name: String,
    pub backend_urls: BackendUrls,
}

#[derive(Debug, Clone, Deserialize)]
pub struct BackendUrls {
    pub supervisor_ws: String,
    pub agent_ws: String,
}

/// Top-level entry from `main.rs` when `--bootstrap <token>` is given.
pub async fn run_bootstrap(install_token: String, backend_url: String) -> Result<()> {
    if install_token.is_empty() {
        bail!("--bootstrap requires a non-empty install_token");
    }
    if backend_url.is_empty() {
        bail!("--backend-url is required when --bootstrap is given");
    }

    info!("==> 1/6 Exchanging install_token for sv_/ta_ tokens");
    let response = exchange(&install_token, &backend_url).await?;
    info!(
        supervisor_id = %response.supervisor_id,
        agent_id = %response.agent_id,
        machine = %response.supervisor_name,
        "exchange ok"
    );

    let bin_dir = supervisor_install_dir()?;
    let cfg_dir = supervisor_config_dir()?;
    let cfg_path = cfg_dir.join("config.toml");

    fs::create_dir_all(&bin_dir).await.with_context(|| {
        format!("create supervisor bin dir {}", bin_dir.display())
    })?;
    fs::create_dir_all(&cfg_dir).await.with_context(|| {
        format!("create supervisor config dir {}", cfg_dir.display())
    })?;

    // Compute managed_dir up front so the config we write and the
    // dir we download into are guaranteed in sync.
    let agent_cfg = AgentConfig {
        mode: AgentMode::Managed,
        cwd: PathBuf::from("."), // overwritten by managed_agent_dir
        url: response.backend_urls.agent_ws.clone(),
        token: response.agent_token.clone(),
        exec_path: None,
        upgrade_target_path: None,
        managed_dir: None, // → fall back to platform default
        update_channel: "stable".into(),
        update_check_interval_s: 3600,
    };
    let managed_dir = managed_agent_dir(&agent_cfg);

    info!(path = %cfg_path.display(), "==> 2/6 Writing config.toml");
    if cfg_path.exists() {
        let backup = cfg_path.with_extension(format!(
            "toml.bak.{}",
            chrono::Utc::now().format("%Y%m%dT%H%M%SZ")
        ));
        fs::rename(&cfg_path, &backup)
            .await
            .with_context(|| format!("backup existing config to {}", backup.display()))?;
        info!(backup = %backup.display(), "existing config backed up");
    }
    let toml = render_config(&response, &managed_dir);
    fs::write(&cfg_path, toml)
        .await
        .with_context(|| format!("write {}", cfg_path.display()))?;

    info!(dir = %managed_dir.display(), "==> 3/6 Downloading latest agent binary");
    fs::create_dir_all(&managed_dir).await.with_context(|| {
        format!("create managed agent dir {}", managed_dir.display())
    })?;
    let agent_exe = managed_dir.join(default_managed_agent_exe_name());
    let (os_type, arch) = current_platform()?;
    let release = install_latest(
        &backend_url,
        &response.agent_token,
        os_type,
        "stable",
        arch,
        &agent_exe,
    )
    .await
    .context("install latest agent binary")?;
    info!(version = %release.version, path = %agent_exe.display(), "agent binary in place");

    info!("==> 4/6 Determining own executable path");
    let supervisor_exe = std::env::current_exe()
        .context("locate own executable for service registration")?;
    let installed_supervisor_exe = bin_dir.join(supervisor_exe_name());
    // If the supervisor was downloaded by the install script and is
    // already at $bin_dir/<exe>, skip the copy (idempotent re-run).
    if supervisor_exe != installed_supervisor_exe {
        // The install PowerShell already places the binary at
        // bin_dir/<exe>; if for some reason we're being run from
        // elsewhere (e.g. dev test), copy ourselves in.
        if let Err(e) = fs::copy(&supervisor_exe, &installed_supervisor_exe).await {
            warn!(error = %e, "failed to install own binary into bin_dir; using current path instead");
        }
    }
    let final_exe = if installed_supervisor_exe.exists() {
        installed_supervisor_exe
    } else {
        supervisor_exe
    };

    #[cfg(windows)]
    {
        info!("==> 5/6 Registering Scheduled Task (ONLOGON)");
        if let Err(e) = register_logon_task(&final_exe, &cfg_path).await {
            warn!(error = %e, "Scheduled Task registration failed; supervisor will need manual start on next logon");
        } else {
            info!("Scheduled Task 'MCPWorkspaceSupervisor' registered");
        }
    }
    #[cfg(not(windows))]
    {
        info!("==> 5/6 Skipping autostart (POSIX systemd unit not implemented yet)");
    }

    info!("==> 6/6 Spawning supervisor in background");
    spawn_supervisor_detached(&final_exe, &cfg_path)
        .await
        .context("spawn supervisor")?;

    println!(
        "OK install complete: supervisor_id={} agent_id={} machine={} agent_version={}",
        response.supervisor_id, response.agent_id, response.supervisor_name, release.version
    );
    Ok(())
}

async fn exchange(install_token: &str, backend_url: &str) -> Result<ExchangeResponse> {
    let url = format!(
        "{}/api/v1/workspaces/supervisors/exchange",
        backend_url.trim_end_matches('/')
    );
    let client = reqwest::Client::builder()
        .https_only(false)
        .build()
        .context("build reqwest client")?;
    let resp = client
        .post(&url)
        .header("X-Install-Token", install_token)
        .send()
        .await
        .context("POST /supervisors/exchange")?;
    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        bail!("exchange returned HTTP {status}: {body}");
    }
    let parsed: ExchangeResponse = resp.json().await.context("parse exchange JSON")?;
    Ok(parsed)
}

fn render_config(response: &ExchangeResponse, managed_dir: &Path) -> String {
    // No TOML library serialization here: we want a stable, human-
    // readable layout that mirrors `prod-config.toml` exactly so an
    // operator can hand-edit later without surprises.
    let managed_dir_str = managed_dir.display().to_string().replace('\\', "/");
    format!(
        r#"# Auto-generated by mcp-workspace-supervisor --bootstrap.
# Machine: {machine}
# Supervisor: {sv_id}
# Agent:      {agent_id}
#
# Edit `agent.update_channel` to "beta" / "canary" to receive newer
# pre-release agent binaries.

[backend]
url = "{sv_ws}"
token = "{sv_token}"
heartbeat_interval_s = 30

[agent]
mode = "managed"
managed_dir = "{managed_dir}"
url = "{agent_ws}"
token = "{agent_token}"
update_channel = "stable"
update_check_interval_s = 3600
# `cwd` is computed from `managed_dir` at runtime; this value is just
# a placeholder so legacy code paths that read it don't panic.
cwd = "{managed_dir}"

[log]
ring_capacity = 10000
file_path = ""
max_line_bytes = 4096
subscriber_channel_capacity = 256

[restart]
backoff_initial_ms = 1000
backoff_max_ms = 32000
backoff_jitter_pct = 20
graceful_timeout_ms = 5000

[supervisor_log]
dir = ""
rotation_size_mb = 10
rotation_keep = 5
"#,
        machine = response.supervisor_name,
        sv_id = response.supervisor_id,
        agent_id = response.agent_id,
        sv_ws = response.backend_urls.supervisor_ws,
        sv_token = response.supervisor_token,
        agent_ws = response.backend_urls.agent_ws,
        agent_token = response.agent_token,
        managed_dir = managed_dir_str,
    )
}

fn supervisor_exe_name() -> &'static str {
    if cfg!(windows) {
        "mcp-workspace-supervisor.exe"
    } else {
        "mcp-workspace-supervisor"
    }
}

#[cfg(windows)]
async fn register_logon_task(exe: &Path, cfg: &Path) -> Result<()> {
    // We invoke `schtasks /Create /F` so re-runs overwrite cleanly.
    // First try with /RL HIGHEST (required for some elevated agent
    // operations later); on failure (typical when the install was
    // run from a non-elevated PowerShell), retry without /RL — the
    // task still runs on logon, just at standard user level, which
    // is fine for the supervisor's needs.
    let task_run = format!(
        "\"{}\" --config \"{}\"",
        exe.display(),
        cfg.display()
    );

    // Attempt #1: HIGHEST (works under elevated PowerShell).
    if try_schtasks_create(&task_run, true).await.is_ok() {
        return Ok(());
    }
    // Attempt #2: standard user level (works without elevation).
    try_schtasks_create(&task_run, false).await.context(
        "schtasks /Create failed at both HIGHEST and standard privilege levels"
    )
}

#[cfg(windows)]
async fn try_schtasks_create(task_run: &str, highest: bool) -> Result<()> {
    let mut cmd = AsyncCommand::new("schtasks");
    cmd.args([
        "/Create", "/SC", "ONLOGON",
        "/TN", "MCPWorkspaceSupervisor",
    ]);
    if highest {
        cmd.args(["/RL", "HIGHEST"]);
    }
    cmd.arg("/F");
    cmd.arg("/TR").arg(task_run);
    let status = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .await
        .context("invoke schtasks /Create")?;
    if !status.success() {
        bail!("schtasks /Create exited with status {status}");
    }
    Ok(())
}

async fn spawn_supervisor_detached(exe: &Path, cfg: &Path) -> Result<()> {
    // NB: do *not* taskkill /IM mcp-workspace-supervisor.exe here —
    // the install PowerShell already stopped prior instances before
    // invoking --bootstrap, and we ARE a process named
    // mcp-workspace-supervisor.exe so a /IM kill would terminate
    // ourselves before we finish spawning. (Earlier v0.3.0 bug.)

    #[cfg(windows)]
    let mut cmd = {
        // DETACHED_PROCESS so the new process has no console;
        // CREATE_NEW_PROCESS_GROUP so Ctrl-C doesn't propagate from
        // the install script; CREATE_BREAKAWAY_FROM_JOB so the
        // supervisor outlives any Job Object the parent (PowerShell)
        // belongs to.
        use std::os::windows::process::CommandExt as _;
        const DETACHED_PROCESS: u32 = 0x0000_0008;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;
        let mut c = AsyncCommand::new(exe);
        c.creation_flags(
            DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
        );
        c
    };

    #[cfg(not(windows))]
    let mut cmd = {
        use std::os::unix::process::CommandExt;
        let mut c = AsyncCommand::new(exe);
        c.process_group(0); // setsid
        c
    };

    cmd.args(["--config", cfg.display().to_string().as_str()])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    let child = cmd.spawn().context("spawn supervisor")?;
    let pid = child.id().unwrap_or(0);
    info!(pid, "supervisor spawned (detached)");
    // We deliberately do NOT await `child.wait()` — the supervisor is
    // intended to outlive this bootstrap process. Dropping `child`
    // detaches the handle without killing the process.
    drop(child);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dummy_response() -> ExchangeResponse {
        ExchangeResponse {
            supervisor_id: "sv-id".into(),
            supervisor_token: "sv_yyy".into(),
            supervisor_name: "TESTHOST".into(),
            agent_id: "ag-id".into(),
            agent_token: "ta_xxx".into(),
            agent_name: "TESTHOST-agent".into(),
            backend_urls: BackendUrls {
                supervisor_ws: "wss://example/sup/ws".into(),
                agent_ws: "wss://example/agent/ws".into(),
            },
        }
    }

    #[test]
    fn render_config_includes_both_tokens_and_urls() {
        let toml = render_config(&dummy_response(), Path::new("C:/install/agent"));
        assert!(toml.contains("[backend]"));
        assert!(toml.contains("[agent]"));
        assert!(toml.contains("mode = \"managed\""));
        assert!(toml.contains("token = \"sv_yyy\""));
        assert!(toml.contains("token = \"ta_xxx\""));
        assert!(toml.contains("wss://example/sup/ws"));
        assert!(toml.contains("wss://example/agent/ws"));
        assert!(toml.contains("update_channel = \"stable\""));
        assert!(toml.contains("managed_dir = \"C:/install/agent\""));
    }

    #[test]
    fn render_config_normalises_backslashes_to_forward_slash() {
        // Windows paths in TOML should use `/` so the supervisor's
        // own TOML parser doesn't have to interpret `\` escapes.
        let toml = render_config(
            &dummy_response(),
            Path::new(r"C:\Users\me\AppData\Local\mcp-workspace\agent"),
        );
        assert!(toml.contains("/Users/me/AppData/Local/mcp-workspace/agent"));
        assert!(!toml.contains(r"\Users\me\AppData"));
    }
}
