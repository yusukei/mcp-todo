//! Self-update via rename-swap.
//!
//! Rust port of `agent/self_update.py`. Server pushes an
//! `update_available` frame with `{download_url, sha256, version}`;
//! the agent downloads to `<exe>.new`, verifies the hash incrementally,
//! atomically renames the running exe to `<exe>.old.<ts>`, swaps the
//! new file into place, spawns a detached child, and exits.
//!
//! Why rename-swap and not in-place overwrite: on Windows the running
//! .exe is locked, but the file *can* be renamed if the open handle
//! used `FILE_SHARE_DELETE` (which is the default). The `.old.<ts>`
//! sidecar is kept on disk so a failed spawn can be rolled back, and
//! the next startup's [`cleanup_old_files`] sweeps it away.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;

use futures_util::StreamExt;
use sha2::{Digest, Sha256};
use thiserror::Error;
use tokio::fs;
use tokio::io::AsyncWriteExt;
use tracing::{debug, info, warn};

const SKIP_CLEANUP_ENV: &str = "MCP_AGENT_SKIP_UPDATE_CLEANUP";
const DOWNLOAD_TIMEOUT_SECS: u64 = 300;
/// Brief sleep between downloading the new binary and renaming the
/// running exe out of the way. Lets Windows Defender / other AV
/// inspect the file before CreateProcess tries to launch it. Without
/// this, aggressive AV can fail the spawn with ERROR_VIRUS_INFECTED
/// on a benign file.
const POST_DOWNLOAD_SLEEP_MS: u64 = 500;

#[derive(Debug, Error)]
pub enum UpdateError {
    #[error("download failed: {0}")]
    Download(String),
    #[error("sha256 mismatch: expected {expected}, got {got}")]
    Sha256Mismatch { expected: String, got: String },
    #[error("io error at {path}: {source}")]
    Io {
        path: String,
        #[source]
        source: std::io::Error,
    },
    #[error("spawn failed: {0}")]
    Spawn(String),
}

/// Return the on-disk path of the running agent binary.
pub fn current_executable_path() -> std::io::Result<PathBuf> {
    std::env::current_exe()
}

/// Remove leftover `<exe>.new` and `<exe>.old.*` from previous updates.
/// Returns the number of files actually deleted; failures are logged
/// at debug level and ignored.
pub fn cleanup_old_files(exe: Option<&Path>) -> usize {
    if std::env::var_os(SKIP_CLEANUP_ENV).is_some() {
        return 0;
    }
    let exe = match exe {
        Some(p) => p.to_path_buf(),
        None => match current_executable_path() {
            Ok(p) => p,
            Err(_) => return 0,
        },
    };
    let parent = match exe.parent() {
        Some(p) if !p.as_os_str().is_empty() => p.to_path_buf(),
        _ => return 0,
    };
    let stem = match exe.file_name().and_then(|s| s.to_str()) {
        Some(s) => s.to_string(),
        None => return 0,
    };
    let new_name = format!("{stem}.new");
    let old_prefix = format!("{stem}.old");

    let read = match std::fs::read_dir(&parent) {
        Ok(r) => r,
        Err(_) => return 0,
    };
    let mut removed = 0;
    for ent in read.flatten() {
        let name = match ent.file_name().to_str() {
            Some(s) => s.to_string(),
            None => continue,
        };
        if name == new_name || name.starts_with(&old_prefix) {
            match std::fs::remove_file(ent.path()) {
                Ok(()) => {
                    info!(path = %ent.path().display(), "removed stale update artifact");
                    removed += 1;
                }
                Err(e) => {
                    // .old.exe may still be locked by a not-quite-dead
                    // predecessor on Windows. Try again next start.
                    debug!(path = %ent.path().display(), error = %e, "could not remove stale artifact");
                }
            }
        }
    }
    removed
}

/// Stream the response body to `dest` while computing SHA-256 in 1 MB
/// chunks. On hash mismatch the partial file is removed before
/// returning.
pub async fn download_with_sha256(
    url: &str,
    dest: &Path,
    expected_sha256: &str,
    token: Option<&str>,
) -> Result<usize, UpdateError> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(DOWNLOAD_TIMEOUT_SECS))
        .build()
        .map_err(|e| UpdateError::Download(e.to_string()))?;
    let mut req = client.get(url).header(
        reqwest::header::USER_AGENT,
        format!("mcp-workspace-agent-rs/{}", env!("CARGO_PKG_VERSION")),
    );
    if let Some(tok) = token {
        req = req.bearer_auth(tok);
    }
    let resp = req
        .send()
        .await
        .map_err(|e| UpdateError::Download(e.to_string()))?;
    if !resp.status().is_success() {
        return Err(UpdateError::Download(format!(
            "HTTP {} from {url}",
            resp.status()
        )));
    }
    let mut hasher = Sha256::new();
    let mut written = 0usize;
    let mut file = fs::File::create(dest).await.map_err(|e| UpdateError::Io {
        path: dest.display().to_string(),
        source: e,
    })?;
    let mut stream = resp.bytes_stream();
    while let Some(chunk_res) = stream.next().await {
        let chunk = chunk_res.map_err(|e| UpdateError::Download(e.to_string()))?;
        hasher.update(&chunk);
        file.write_all(&chunk).await.map_err(|e| UpdateError::Io {
            path: dest.display().to_string(),
            source: e,
        })?;
        written += chunk.len();
    }
    file.flush().await.map_err(|e| UpdateError::Io {
        path: dest.display().to_string(),
        source: e,
    })?;
    drop(file);

    let got = hex_lower(&hasher.finalize());
    if !got.eq_ignore_ascii_case(expected_sha256) {
        let _ = std::fs::remove_file(dest);
        return Err(UpdateError::Sha256Mismatch {
            expected: expected_sha256.to_string(),
            got,
        });
    }
    Ok(written)
}

fn hex_lower(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Run the full update flow: download → verify → rename-swap → spawn.
/// Returns the path of the relocated old binary so the caller can log
/// where it went (cleanup will remove it on the next start).
pub async fn apply_update(
    download_url: &str,
    sha256: &str,
    version: Option<&str>,
    token: Option<&str>,
    restart_argv: Vec<String>,
    exe_path: Option<PathBuf>,
) -> Result<PathBuf, UpdateError> {
    let target = match exe_path {
        Some(p) => p,
        None => current_executable_path().map_err(|e| UpdateError::Io {
            path: "<current_exe>".into(),
            source: e,
        })?,
    };
    let target = dunce::canonicalize(&target).unwrap_or(target);

    let new_path = with_suffix(&target, ".new");
    let ts = chrono::Utc::now().timestamp();
    let old_path = with_suffix(&target, &format!(".old.{ts}"));

    if new_path.exists() {
        std::fs::remove_file(&new_path).map_err(|e| UpdateError::Io {
            path: new_path.display().to_string(),
            source: e,
        })?;
    }

    info!(
        version = ?version,
        url = %download_url,
        target = %target.display(),
        "downloading update"
    );
    let n = download_with_sha256(download_url, &new_path, sha256, token).await?;
    info!(bytes = n, "downloaded; sha256 verified");

    if POST_DOWNLOAD_SLEEP_MS > 0 {
        tokio::time::sleep(Duration::from_millis(POST_DOWNLOAD_SLEEP_MS)).await;
    }

    // Step 1: move the running exe aside.
    if let Err(e) = std::fs::rename(&target, &old_path) {
        let _ = std::fs::remove_file(&new_path); // clean up staged .new
        return Err(UpdateError::Io {
            path: format!("rename {} -> {}", target.display(), old_path.display()),
            source: e,
        });
    }
    // Step 2: promote the new binary.
    if let Err(e) = std::fs::rename(&new_path, &target) {
        // Rollback so the agent can keep running on the old exe.
        if let Err(rb) = std::fs::rename(&old_path, &target) {
            warn!(
                err = %e,
                rollback_err = %rb,
                "rollback failed after promotion error — exe may be missing"
            );
        }
        return Err(UpdateError::Io {
            path: format!("rename {} -> {}", new_path.display(), target.display()),
            source: e,
        });
    }
    // Step 3: spawn the replacement.
    spawn_detached(&target, &restart_argv)?;
    info!(old = %old_path.display(), "update applied");
    Ok(old_path)
}

fn with_suffix(p: &Path, suffix: &str) -> PathBuf {
    let mut name = p
        .file_name()
        .map(|s| s.to_os_string())
        .unwrap_or_default();
    name.push(suffix);
    p.with_file_name(name)
}

#[cfg(unix)]
fn spawn_detached(exe: &Path, args: &[String]) -> Result<(), UpdateError> {
    use std::os::unix::process::CommandExt;
    std::process::Command::new(exe)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0)
        .spawn()
        .map(|_| ())
        .map_err(|e| UpdateError::Spawn(e.to_string()))
}

#[cfg(windows)]
fn spawn_detached(exe: &Path, args: &[String]) -> Result<(), UpdateError> {
    use std::os::windows::process::CommandExt;
    const DETACHED_PROCESS: u32 = 0x0000_0008;
    const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
    const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;
    let base = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP;

    let mut cmd = std::process::Command::new(exe);
    cmd.args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    // Try with breakaway first so the child survives even when the
    // parent runs inside a Windows Job Object (services, schedulers,
    // CI). If breakaway is denied, retry without it — that's enough
    // for interactive starts and keeps the binary upgrade path
    // unblocked.
    match cmd.creation_flags(base | CREATE_BREAKAWAY_FROM_JOB).spawn() {
        Ok(_) => Ok(()),
        Err(e) => {
            warn!(error = %e, "spawn with CREATE_BREAKAWAY_FROM_JOB failed; retrying without");
            std::process::Command::new(exe)
                .args(args)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .creation_flags(base)
                .spawn()
                .map(|_| ())
                .map_err(|e| UpdateError::Spawn(e.to_string()))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn write_fake_exe(d: &TempDir, name: &str, body: &[u8]) -> PathBuf {
        let p = d.path().join(name);
        std::fs::write(&p, body).unwrap();
        p
    }

    #[test]
    fn cleanup_removes_new_and_old_artifacts() {
        let d = tempfile::tempdir().unwrap();
        let exe = write_fake_exe(&d, "mcp-workspace-agent-rs.exe", b"\x7fELF");
        // Sibling artifacts that should be removed.
        std::fs::write(d.path().join("mcp-workspace-agent-rs.exe.new"), b"new").unwrap();
        std::fs::write(d.path().join("mcp-workspace-agent-rs.exe.old.123"), b"old1")
            .unwrap();
        std::fs::write(d.path().join("mcp-workspace-agent-rs.exe.old.456"), b"old2")
            .unwrap();
        // Unrelated file that must NOT be touched.
        std::fs::write(d.path().join("config.toml"), b"k=v").unwrap();

        let n = cleanup_old_files(Some(&exe));
        assert_eq!(n, 3);
        assert!(exe.exists(), "live exe must survive cleanup");
        assert!(d.path().join("config.toml").exists(), "unrelated file untouched");
        assert!(!d.path().join("mcp-workspace-agent-rs.exe.new").exists());
        assert!(!d.path().join("mcp-workspace-agent-rs.exe.old.123").exists());
        assert!(!d.path().join("mcp-workspace-agent-rs.exe.old.456").exists());
    }

    #[test]
    fn cleanup_skipped_via_env() {
        let d = tempfile::tempdir().unwrap();
        let exe = write_fake_exe(&d, "agent.exe", b"x");
        std::fs::write(d.path().join("agent.exe.new"), b"x").unwrap();
        std::env::set_var(SKIP_CLEANUP_ENV, "1");
        let n = cleanup_old_files(Some(&exe));
        std::env::remove_var(SKIP_CLEANUP_ENV);
        assert_eq!(n, 0);
        assert!(d.path().join("agent.exe.new").exists());
    }

    #[test]
    fn cleanup_no_artifacts_to_remove() {
        let d = tempfile::tempdir().unwrap();
        let exe = write_fake_exe(&d, "agent.exe", b"x");
        let n = cleanup_old_files(Some(&exe));
        assert_eq!(n, 0);
        assert!(exe.exists());
    }

    #[test]
    fn cleanup_missing_parent_returns_zero() {
        // Path pointing into a non-existent directory.
        let p = std::path::Path::new("/this/does/not/exist/agent.exe");
        let n = cleanup_old_files(Some(p));
        assert_eq!(n, 0);
    }

    #[test]
    fn with_suffix_appends_correctly() {
        let p = Path::new("/tmp/foo.exe");
        assert_eq!(with_suffix(p, ".new"), Path::new("/tmp/foo.exe.new"));
        assert_eq!(
            with_suffix(p, ".old.123"),
            Path::new("/tmp/foo.exe.old.123")
        );
    }

    #[test]
    fn hex_lower_matches_known_value() {
        // sha256("") = e3b0c44...
        let mut h = Sha256::new();
        h.update(b"");
        assert_eq!(
            hex_lower(&h.finalize()),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }
}
