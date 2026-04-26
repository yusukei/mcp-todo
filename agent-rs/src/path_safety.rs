//! Path traversal protection.
//!
//! Rust port of `_resolve_safe_path` / `_resolve_safe_dir` from
//! `agent/main.py:199-254`. Both functions resolve a user-supplied
//! path against `cwd` and ensure the result stays inside the workspace.
//!
//! - [`resolve_safe_path`] requires the target to exist (canonicalize
//!   the entire path). Used by read-only handlers.
//! - [`resolve_safe_dir`] allows the leaf — and any number of
//!   intermediate directories — to be missing. Used by mkdir / write /
//!   move destinations where the agent will create the path itself.
//!
//! Both reject NUL bytes, missing/empty `cwd`, non-existent `cwd`, and
//! any traversal that escapes the canonicalized base.

// Handlers (`agent-rs/03` and later) are the consumers; until they
// land this module is reachable only from its own tests.
#![allow(dead_code)]

use std::path::{Component, Path, PathBuf};

use thiserror::Error;

/// Errors returned by path-safety resolution. Field-free variants
/// match Python's `ValueError("...")` message tokens (`cwd`,
/// `does not exist`, `NUL byte`, `traversal`) so existing handler
/// error formatting stays close to the Python original.
#[derive(Debug, Error, PartialEq, Eq)]
pub enum PathSafetyError {
    #[error("cwd is required")]
    CwdRequired,
    #[error("Working directory does not exist: {0}")]
    CwdNotADir(String),
    #[error("Invalid path: contains NUL byte")]
    NulByte,
    #[error("Path traversal not allowed")]
    Traversal,
}

/// Lexically normalize an absolute path: collapse `.` / `..` without
/// touching the filesystem. Mirrors `os.path.abspath`'s normalization
/// step, which is what catches `../../etc` even when the parent
/// doesn't exist on disk.
fn lexical_normalize(p: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for comp in p.components() {
        match comp {
            Component::CurDir => {}
            Component::ParentDir => {
                // pop is a no-op on a bare root — that's fine, since
                // `..` past root is undefined and Python's normpath
                // also clamps there.
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

fn resolve_base(cwd: Option<&str>) -> Result<PathBuf, PathSafetyError> {
    let cwd = cwd
        .filter(|s| !s.is_empty())
        .ok_or(PathSafetyError::CwdRequired)?;
    let canon = dunce::canonicalize(Path::new(cwd))
        .map_err(|_| PathSafetyError::CwdNotADir(cwd.to_string()))?;
    if !canon.is_dir() {
        return Err(PathSafetyError::CwdNotADir(cwd.to_string()));
    }
    Ok(canon)
}

/// Resolve `path` against `cwd` and confirm it points inside `cwd`.
/// The target must exist — symlinks are followed by [`dunce::canonicalize`]
/// so a link out of the workspace is detected and rejected.
pub fn resolve_safe_path(
    path: &str,
    cwd: Option<&str>,
) -> Result<PathBuf, PathSafetyError> {
    if path.contains('\0') {
        return Err(PathSafetyError::NulByte);
    }
    let base = resolve_base(cwd)?;
    let p = Path::new(path);
    let candidate: PathBuf = if p.is_absolute() {
        p.to_path_buf()
    } else {
        base.join(p)
    };
    let resolved =
        dunce::canonicalize(&candidate).map_err(|_| PathSafetyError::Traversal)?;
    if !resolved.starts_with(&base) {
        return Err(PathSafetyError::Traversal);
    }
    Ok(resolved)
}

/// Resolve `path` against `cwd` for a destination that may not exist
/// yet. The leaf and any number of missing intermediate directories
/// are allowed; the longest existing ancestor is canonicalized so a
/// symlink-to-outside still gets rejected, and the missing tail is
/// reattached lexically.
pub fn resolve_safe_dir(
    path: &str,
    cwd: Option<&str>,
) -> Result<PathBuf, PathSafetyError> {
    if path.contains('\0') {
        return Err(PathSafetyError::NulByte);
    }
    let base = resolve_base(cwd)?;
    let p = Path::new(path);
    let candidate: PathBuf = if p.is_absolute() {
        p.to_path_buf()
    } else {
        base.join(p)
    };
    // Lexically normalize first so `..` segments collapse before we
    // hit the filesystem. Without this, a path like
    // `cwd/sub/../../etc/passwd` would canonicalize the existing
    // `cwd/sub` ancestor and miss the escape.
    let normalized = lexical_normalize(&candidate);
    let parent = normalized.parent().unwrap_or(base.as_path());
    let resolved_parent = canonicalize_existing_ancestor(parent)?;
    if !resolved_parent.starts_with(&base) {
        return Err(PathSafetyError::Traversal);
    }
    let basename = normalized
        .file_name()
        .ok_or(PathSafetyError::Traversal)?;
    Ok(resolved_parent.join(basename))
}

/// Canonicalize the longest existing prefix of `p` and re-append the
/// missing tail components verbatim. Falls back to the lexical path
/// when nothing on it exists, so the caller's `starts_with(base)`
/// check still catches escapes via missing dirs.
fn canonicalize_existing_ancestor(p: &Path) -> Result<PathBuf, PathSafetyError> {
    let mut current = p.to_path_buf();
    let mut suffix: Vec<std::ffi::OsString> = Vec::new();
    loop {
        if let Ok(canon) = dunce::canonicalize(&current) {
            let mut result = canon;
            for c in suffix.iter().rev() {
                result.push(c);
            }
            return Ok(result);
        }
        let Some(last) = current.file_name().map(|f| f.to_os_string()) else {
            // Reached the root without finding an existing ancestor —
            // return the lexical path. The starts_with check will
            // still catch any escape because `..` was already collapsed.
            let mut result = current;
            for c in suffix.iter().rev() {
                result.push(c);
            }
            return Ok(result);
        };
        suffix.push(last);
        if !current.pop() {
            let mut result = current;
            for c in suffix.iter().rev() {
                result.push(c);
            }
            return Ok(result);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    fn tmp() -> TempDir {
        tempfile::tempdir().expect("create tempdir")
    }

    fn cwd_str(d: &TempDir) -> String {
        d.path().to_string_lossy().into_owned()
    }

    fn canon(p: &Path) -> PathBuf {
        dunce::canonicalize(p).expect("canonicalize")
    }

    // ── resolve_safe_path: success cases ─────────────────────────

    #[test]
    fn relative_path_inside_cwd() {
        let d = tmp();
        fs::write(d.path().join("file.txt"), "ok").unwrap();
        let resolved =
            resolve_safe_path("file.txt", Some(&cwd_str(&d))).unwrap();
        assert_eq!(resolved, canon(&d.path().join("file.txt")));
    }

    #[test]
    fn nested_relative_path() {
        let d = tmp();
        let sub = d.path().join("sub");
        fs::create_dir(&sub).unwrap();
        fs::write(sub.join("f.txt"), "ok").unwrap();
        let resolved =
            resolve_safe_path("sub/f.txt", Some(&cwd_str(&d))).unwrap();
        assert_eq!(resolved, canon(&sub.join("f.txt")));
    }

    #[test]
    fn absolute_path_inside_cwd() {
        let d = tmp();
        let target = d.path().join("abs.txt");
        fs::write(&target, "ok").unwrap();
        let target_str = target.to_string_lossy().into_owned();
        let resolved =
            resolve_safe_path(&target_str, Some(&cwd_str(&d))).unwrap();
        assert_eq!(resolved, canon(&target));
    }

    #[test]
    fn dot_path_resolves_to_cwd() {
        let d = tmp();
        let resolved = resolve_safe_path(".", Some(&cwd_str(&d))).unwrap();
        assert_eq!(resolved, canon(d.path()));
    }

    // ── resolve_safe_path: traversal rejections ──────────────────

    #[test]
    fn dotdot_traversal_rejected() {
        let d = tmp();
        let sub = d.path().join("sub");
        fs::create_dir(&sub).unwrap();
        let cwd = sub.to_string_lossy().into_owned();
        let err = resolve_safe_path("../outside.txt", Some(&cwd)).unwrap_err();
        assert_eq!(err, PathSafetyError::Traversal);
    }

    #[test]
    fn deep_dotdot_traversal_rejected() {
        let d = tmp();
        let sub = d.path().join("a").join("b");
        fs::create_dir_all(&sub).unwrap();
        let cwd = sub.to_string_lossy().into_owned();
        let err = resolve_safe_path("../../../etc/passwd", Some(&cwd))
            .unwrap_err();
        assert_eq!(err, PathSafetyError::Traversal);
    }

    #[test]
    #[cfg(unix)]
    fn absolute_outside_cwd_rejected_unix() {
        let d = tmp();
        let err = resolve_safe_path("/etc/passwd", Some(&cwd_str(&d)))
            .unwrap_err();
        assert_eq!(err, PathSafetyError::Traversal);
    }

    #[test]
    #[cfg(windows)]
    fn absolute_outside_cwd_rejected_windows() {
        let d = tmp();
        let err = resolve_safe_path(
            r"C:\Windows\System32\drivers\etc\hosts",
            Some(&cwd_str(&d)),
        )
        .unwrap_err();
        assert_eq!(err, PathSafetyError::Traversal);
    }

    /// Cross-drive paths on Windows must be rejected — Python's
    /// `os.path.commonpath` raises ValueError; our `Path::starts_with`
    /// returns false → both end at Traversal.
    #[test]
    #[cfg(windows)]
    fn cross_drive_rejected_windows() {
        let d = tmp();
        // Pick a drive letter unlikely to match the temp drive.
        let other = if cwd_str(&d).to_uppercase().starts_with("C:") {
            r"D:\some\path"
        } else {
            r"C:\Windows"
        };
        let err = resolve_safe_path(other, Some(&cwd_str(&d))).unwrap_err();
        assert_eq!(err, PathSafetyError::Traversal);
    }

    // ── cwd validation ───────────────────────────────────────────

    #[test]
    fn missing_cwd_rejected() {
        let err = resolve_safe_path("foo.txt", None).unwrap_err();
        assert_eq!(err, PathSafetyError::CwdRequired);
    }

    #[test]
    fn empty_cwd_rejected() {
        let err = resolve_safe_path("foo.txt", Some("")).unwrap_err();
        assert_eq!(err, PathSafetyError::CwdRequired);
    }

    #[test]
    fn nonexistent_cwd_rejected() {
        let d = tmp();
        let bogus = d.path().join("does-not-exist");
        let err = resolve_safe_path(
            "foo.txt",
            Some(&bogus.to_string_lossy()),
        )
        .unwrap_err();
        match err {
            PathSafetyError::CwdNotADir(_) => {}
            other => panic!("expected CwdNotADir, got {other:?}"),
        }
    }

    #[test]
    fn cwd_is_file_rejected() {
        let d = tmp();
        let file = d.path().join("not-a-dir");
        fs::write(&file, "x").unwrap();
        let err =
            resolve_safe_path("foo", Some(&file.to_string_lossy())).unwrap_err();
        match err {
            PathSafetyError::CwdNotADir(_) => {}
            other => panic!("expected CwdNotADir, got {other:?}"),
        }
    }

    // ── NUL byte ─────────────────────────────────────────────────

    #[test]
    fn nul_byte_rejected_path() {
        let d = tmp();
        let err = resolve_safe_path("foo\0bar", Some(&cwd_str(&d))).unwrap_err();
        assert_eq!(err, PathSafetyError::NulByte);
    }

    #[test]
    fn nul_byte_rejected_dir() {
        let d = tmp();
        let err = resolve_safe_dir("foo\0bar", Some(&cwd_str(&d))).unwrap_err();
        assert_eq!(err, PathSafetyError::NulByte);
    }

    // ── symlink escape (Unix only — Windows needs admin) ─────────

    #[test]
    #[cfg(unix)]
    fn symlink_escape_rejected() {
        use std::os::unix::fs::symlink;
        let d = tmp();
        let outside = d.path().join("outside");
        fs::create_dir(&outside).unwrap();
        fs::write(outside.join("secret.txt"), "secret").unwrap();
        let cwd = d.path().join("cwd");
        fs::create_dir(&cwd).unwrap();
        symlink(&outside, cwd.join("escape")).unwrap();
        let err = resolve_safe_path(
            "escape/secret.txt",
            Some(&cwd.to_string_lossy()),
        )
        .unwrap_err();
        assert_eq!(err, PathSafetyError::Traversal);
    }

    // ── resolve_safe_dir: missing-leaf success ───────────────────

    #[test]
    fn safe_dir_missing_leaf_inside_cwd() {
        let d = tmp();
        let resolved =
            resolve_safe_dir("new_file.txt", Some(&cwd_str(&d))).unwrap();
        assert_eq!(resolved, canon(d.path()).join("new_file.txt"));
    }

    #[test]
    fn safe_dir_missing_intermediate_dir_ok() {
        // `parent/new_file.txt` where `parent` doesn't exist yet —
        // write_file's caller will create it, so we must allow this.
        let d = tmp();
        let resolved =
            resolve_safe_dir("parent/new_file.txt", Some(&cwd_str(&d)))
                .unwrap();
        assert_eq!(
            resolved,
            canon(d.path()).join("parent").join("new_file.txt")
        );
    }

    #[test]
    fn safe_dir_existing_leaf_ok() {
        let d = tmp();
        fs::write(d.path().join("exists.txt"), "x").unwrap();
        let resolved =
            resolve_safe_dir("exists.txt", Some(&cwd_str(&d))).unwrap();
        assert_eq!(resolved, canon(d.path()).join("exists.txt"));
    }

    // ── resolve_safe_dir: traversal rejections ───────────────────

    #[test]
    fn safe_dir_dotdot_escape_rejected() {
        let d = tmp();
        let sub = d.path().join("sub");
        fs::create_dir(&sub).unwrap();
        let err = resolve_safe_dir(
            "../escape.txt",
            Some(&sub.to_string_lossy()),
        )
        .unwrap_err();
        assert_eq!(err, PathSafetyError::Traversal);
    }

    /// Even when the offending path component doesn't exist, the
    /// lexical-normalize step must still catch the escape.
    #[test]
    fn safe_dir_dotdot_via_missing_dir_rejected() {
        let d = tmp();
        let sub = d.path().join("sub");
        fs::create_dir(&sub).unwrap();
        let err = resolve_safe_dir(
            "missing/../../escape.txt",
            Some(&sub.to_string_lossy()),
        )
        .unwrap_err();
        assert_eq!(err, PathSafetyError::Traversal);
    }

    #[test]
    #[cfg(unix)]
    fn safe_dir_absolute_outside_rejected_unix() {
        let d = tmp();
        let err = resolve_safe_dir("/tmp/escape.txt", Some(&cwd_str(&d)))
            .unwrap_err();
        assert_eq!(err, PathSafetyError::Traversal);
    }

    #[test]
    #[cfg(windows)]
    fn safe_dir_absolute_outside_rejected_windows() {
        let d = tmp();
        let err = resolve_safe_dir(r"C:\escape.txt", Some(&cwd_str(&d)));
        // On Windows, C:\escape.txt may or may not exist. Either way,
        // the lexical-normalize + starts_with check should reject it
        // unless the temp dir happens to live at the drive root —
        // which it never does in practice.
        assert_eq!(err.unwrap_err(), PathSafetyError::Traversal);
    }

    // ── resolve_safe_dir: missing cwd ────────────────────────────

    #[test]
    fn safe_dir_missing_cwd_rejected() {
        let err = resolve_safe_dir("foo.txt", None).unwrap_err();
        assert_eq!(err, PathSafetyError::CwdRequired);
    }
}
