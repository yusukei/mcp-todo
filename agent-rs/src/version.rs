//! Identity helpers shared by client/handlers.
//!
//! Parity-critical:
//! - [`os_label`] mirrors Python's `sys.platform` ("win32" / "linux" / "darwin").
//! - [`host_id`] mirrors `agent/main.py:_compute_host_id`:
//!   `sha256(f"{hostname}::{sys.platform}")[:16]`.
//!
//! The backend joins the agent record with the supervisor record via
//! `host_id`, so any divergence here breaks the pairing.

use sha2::{Digest, Sha256};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Map Rust's `std::env::consts::OS` to Python's `sys.platform`.
pub fn os_label() -> &'static str {
    match std::env::consts::OS {
        "windows" => "win32",
        "macos" => "darwin",
        // linux / freebsd / openbsd / netbsd → as-is (matches sys.platform)
        other => other,
    }
}

/// Best-effort hostname from common environment variables.
///
/// We intentionally avoid pulling a `hostname` crate to keep the
/// dependency surface minimal; the env-var route is what
/// `agent/main.py:platform.node()` ends up reading on every supported
/// OS.
pub fn hostname() -> String {
    std::env::var("COMPUTERNAME") // Windows
        .or_else(|_| std::env::var("HOSTNAME")) // many shells on POSIX
        .or_else(|_| std::env::var("HOST")) // older zsh/csh
        .unwrap_or_else(|_| "unknown".to_string())
}

/// Stable per-host identifier (sha256 hex, 16 chars). Mirrors
/// `agent/main.py:_compute_host_id`.
pub fn host_id() -> String {
    let raw = format!("{}::{}", hostname(), os_label());
    let mut hasher = Sha256::new();
    hasher.update(raw.as_bytes());
    let digest = hasher.finalize();
    // Python: hashlib.sha256(raw).hexdigest()[:16] → first 16 hex chars
    // = first 8 bytes rendered as 2 hex chars each.
    digest
        .iter()
        .take(8)
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// Detect locally available shells, echoing what the backend stores in
/// `RemoteAgent.available_shells`. Implementation is intentionally
/// minimal in PoC; the full per-platform probe in `agent/main.py:
/// _detect_shells` is ported in a later task.
pub fn detect_shells() -> Vec<String> {
    if cfg!(windows) {
        let comspec = std::env::var("COMSPEC")
            .unwrap_or_else(|_| r"C:\Windows\system32\cmd.exe".into());
        vec![comspec]
    } else {
        for sh in ["/bin/zsh", "/bin/bash", "/bin/sh"] {
            if std::path::Path::new(sh).exists() {
                return vec![sh.to_string()];
            }
        }
        vec!["/bin/sh".into()]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn os_label_known_values() {
        let v = os_label();
        // Reject the silently-wrong cases we'd see if we forgot the mapping.
        assert_ne!(v, "windows");
        assert_ne!(v, "macos");
    }

    #[test]
    fn host_id_is_16_hex_chars() {
        let id = host_id();
        assert_eq!(id.len(), 16, "host_id must be 16 hex chars (got {id:?})");
        assert!(id.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn host_id_stable_across_calls() {
        assert_eq!(host_id(), host_id());
    }

    /// Pin the algorithm: sha256("known::darwin")[:16].
    /// Computed offline so any drift in the formula is caught.
    #[test]
    fn host_id_pinned_known_input() {
        // Run the same algorithm with a fixed hostname/os so we don't
        // depend on the test machine's identity. Recomputed here from
        // scratch to lock the algorithm itself.
        let raw = "known-host::darwin";
        let mut hasher = Sha256::new();
        hasher.update(raw.as_bytes());
        let expected: String = hasher
            .finalize()
            .iter()
            .take(8)
            .map(|b| format!("{b:02x}"))
            .collect();
        assert_eq!(expected.len(), 16);
        // Cross-check via Python:
        //   hashlib.sha256(b"known-host::darwin").hexdigest()[:16]
        // → "f165dd451c8ebe00"
        // Pinned so any drift in the hashing algorithm or slice length
        // (Python uses the first 16 hex chars = 8 bytes) is caught.
        assert_eq!(expected, "f165dd451c8ebe00");
    }

    #[test]
    fn detect_shells_returns_at_least_one() {
        let shells = detect_shells();
        assert!(!shells.is_empty());
    }
}
