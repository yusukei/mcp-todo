//! Agent-binary self-management for `mode = "managed"`.
//!
//! Downloads the latest stable agent binary from the backend's
//! AgentRelease endpoint, verifies the SHA-256, and places it where
//! `process::AgentCommand::from_config` expects in managed mode.
//!
//! Reuses the streaming-download pattern from `upgrade.rs` (in-place
//! sha256 computation, atomic rename) but does not need the full
//! `agent.pause()` dance because the bootstrap flow runs *before* the
//! supervised loop starts. Periodic mid-run updates (Phase 4+) will
//! call into `upgrade::run_upgrade` instead.

use std::path::{Path, PathBuf};

use anyhow::{anyhow, bail, Context, Result};
use futures_util::StreamExt;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use tokio::fs;
use tokio::io::AsyncWriteExt;
use tracing::info;

const SHA256_HEX_LEN: usize = 64;

/// Subset of the backend's AgentRelease shape we need to download.
#[derive(Debug, Clone, Deserialize)]
pub struct AgentReleaseInfo {
    pub id: String,
    pub version: String,
    pub sha256: String,
    pub size_bytes: u64,
    pub download_url: String,
}

/// Look up the latest release for the given platform + channel.
///
/// Calls `GET /api/v1/workspaces/releases/latest`. Authenticated with
/// `Bearer <agent_token>` — same auth gate as the agent itself uses,
/// so a bootstrap-time call works as soon as the exchange step has
/// minted a `ta_` token.
pub async fn fetch_latest(
    backend_url: &str,
    agent_token: &str,
    os_type: &str,
    channel: &str,
    arch: &str,
) -> Result<AgentReleaseInfo> {
    let url = format!(
        "{}/api/v1/workspaces/releases/latest?os_type={}&channel={}&arch={}",
        backend_url.trim_end_matches('/'),
        os_type,
        channel,
        arch,
    );
    let client = reqwest::Client::builder()
        .https_only(false) // also accept http:// for staging / local
        .build()
        .context("build reqwest client")?;
    let resp = client
        .get(&url)
        .header("Authorization", format!("Bearer {agent_token}"))
        .send()
        .await
        .context("GET /releases/latest")?;
    if !resp.status().is_success() {
        bail!("GET releases/latest returned HTTP {}", resp.status());
    }
    let info: AgentReleaseInfo = resp.json().await.context("parse releases/latest JSON")?;
    Ok(info)
}

/// Download `info.download_url` to `dest`, verifying SHA-256.
///
/// Streams the body so a multi-megabyte agent binary doesn't double
/// in RAM. On mismatch the destination file is removed and an error
/// returned; the caller is responsible for not leaving stale tmp
/// files when it bails.
pub async fn download_to(
    info: &AgentReleaseInfo,
    agent_token: &str,
    dest: &Path,
) -> Result<()> {
    if info.sha256.len() != SHA256_HEX_LEN
        || !info.sha256.bytes().all(|b| b.is_ascii_hexdigit())
    {
        bail!(
            "release.sha256 must be {SHA256_HEX_LEN} lowercase hex chars (got {:?})",
            info.sha256
        );
    }
    let expected = info.sha256.to_ascii_lowercase();

    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)
            .await
            .with_context(|| format!("create parent dir {}", parent.display()))?;
    }

    let client = reqwest::Client::builder()
        .https_only(false)
        .build()
        .context("build reqwest client")?;
    let resp = client
        .get(&info.download_url)
        .header("Authorization", format!("Bearer {agent_token}"))
        .send()
        .await
        .context("GET download_url")?;
    if !resp.status().is_success() {
        bail!("GET download_url returned HTTP {}", resp.status());
    }

    let mut file = fs::File::create(dest)
        .await
        .with_context(|| format!("open {}", dest.display()))?;
    let mut hasher = Sha256::new();
    let mut stream = resp.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let bytes = chunk.context("read response chunk")?;
        hasher.update(&bytes);
        file.write_all(&bytes)
            .await
            .with_context(|| format!("write to {}", dest.display()))?;
    }
    file.sync_all().await.context("sync_all download")?;
    drop(file);

    let actual: String = hasher
        .finalize()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    if actual != expected {
        let _ = fs::remove_file(dest).await;
        bail!("sha256 mismatch: expected {expected}, got {actual}");
    }

    info!(
        version = %info.version,
        bytes = info.size_bytes,
        path = %dest.display(),
        "agent binary downloaded + sha256 verified"
    );
    Ok(())
}

/// Convenience: fetch_latest + download_to in one call.
///
/// Returns the resolved release info so callers can log / persist
/// the version they ended up with.
pub async fn install_latest(
    backend_url: &str,
    agent_token: &str,
    os_type: &str,
    channel: &str,
    arch: &str,
    dest: &Path,
) -> Result<AgentReleaseInfo> {
    let info = fetch_latest(backend_url, agent_token, os_type, channel, arch).await?;
    download_to(&info, agent_token, dest).await?;
    Ok(info)
}

/// Standard host triple for the install — `("win32", "x64")` etc.
///
/// Mirrors the shape the backend's `os_type` / `arch` columns expect
/// (which in turn mirror `sys.platform` / `platform.machine()` on
/// the agent side).
pub fn current_platform() -> Result<(&'static str, &'static str)> {
    let os = match std::env::consts::OS {
        "windows" => "win32",
        "linux" => "linux",
        "macos" => "darwin",
        other => return Err(anyhow!("unsupported os: {other}")),
    };
    let arch = match std::env::consts::ARCH {
        "x86_64" => "x64",
        "aarch64" => "arm64",
        other => return Err(anyhow!("unsupported arch: {other}")),
    };
    Ok((os, arch))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn download_to_rejects_bad_sha_format() {
        let info = AgentReleaseInfo {
            id: "x".into(),
            version: "0.0.0".into(),
            sha256: "not-hex".into(),
            size_bytes: 0,
            download_url: "http://localhost/never-called".into(),
        };
        let tmp = std::env::temp_dir().join("agent-rs-test-bad-sha");
        let err = download_to(&info, "ta_x", &tmp)
            .await
            .expect_err("bad sha should fail");
        assert!(err.to_string().contains("sha256"));
    }

    #[test]
    fn current_platform_returns_known_pair() {
        let (os, arch) = current_platform().unwrap();
        assert!(matches!(os, "win32" | "linux" | "darwin"));
        assert!(matches!(arch, "x64" | "arm64"));
    }

    /// Pin the type shape so a backend rename of `download_url` /
    /// `sha256` surfaces here (the deserializer would fail otherwise).
    #[test]
    fn agent_release_info_deserializes_known_fields() {
        let json = r#"{
            "id": "abc123",
            "version": "1.2.3",
            "sha256": "deadbeef",
            "size_bytes": 42,
            "download_url": "https://example/dl",
            "os_type": "win32",
            "arch": "x64",
            "channel": "stable",
            "release_notes": "test",
            "uploaded_by": "user-1",
            "created_at": "2026-01-01T00:00:00Z"
        }"#;
        let info: AgentReleaseInfo = serde_json::from_str(json).unwrap();
        assert_eq!(info.id, "abc123");
        assert_eq!(info.version, "1.2.3");
        assert_eq!(info.sha256, "deadbeef");
        assert_eq!(info.size_bytes, 42);
        assert_eq!(info.download_url, "https://example/dl");
    }
}

/// Compute the supervisor binary install dir for the current user.
///
/// Convention (Windows-only for now; matches what `bootstrap.rs`
/// places + what the install PowerShell expects):
///   - `%LOCALAPPDATA%\mcp-workspace\supervisor\`
pub fn supervisor_install_dir() -> Result<PathBuf> {
    if cfg!(windows) {
        let local = std::env::var_os("LOCALAPPDATA")
            .ok_or_else(|| anyhow!("LOCALAPPDATA env var not set"))?;
        Ok(PathBuf::from(local).join("mcp-workspace").join("supervisor"))
    } else {
        let home = std::env::var_os("HOME")
            .ok_or_else(|| anyhow!("HOME env var not set"))?;
        Ok(PathBuf::from(home)
            .join(".local")
            .join("share")
            .join("mcp-workspace")
            .join("supervisor"))
    }
}

/// Compute the supervisor config dir for the current user.
///
///   - `%APPDATA%\mcp-workspace-supervisor\` on Windows (matches the
///     example config / production prod-config.toml location).
///   - `~/.config/mcp-workspace-supervisor/` on POSIX.
pub fn supervisor_config_dir() -> Result<PathBuf> {
    if cfg!(windows) {
        let appdata = std::env::var_os("APPDATA")
            .ok_or_else(|| anyhow!("APPDATA env var not set"))?;
        Ok(PathBuf::from(appdata).join("mcp-workspace-supervisor"))
    } else {
        let home = std::env::var_os("HOME")
            .ok_or_else(|| anyhow!("HOME env var not set"))?;
        Ok(PathBuf::from(home).join(".config").join("mcp-workspace-supervisor"))
    }
}
