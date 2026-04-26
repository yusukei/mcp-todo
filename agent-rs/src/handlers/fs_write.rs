//! Write FS handlers: `write_file` / `mkdir` / `delete` / `move` / `copy`.
//!
//! All blocking I/O runs under `tokio::task::spawn_blocking` so a slow
//! disk doesn't stall the WS read loop. Path safety:
//! - `_safe_dir` (parent canonicalize, leaf may be missing) for new
//!   destinations: `write_file`, `mkdir`, `move.dst`, `copy.dst`
//! - `_safe_path` (target must exist) for sources/operands: `delete`,
//!   `move.src`, `copy.src`

use std::path::Path;

use serde_json::{json, Value};

use super::constants::MAX_FILE_BYTES;
use crate::path_safety::{
    resolve_safe_dir, resolve_safe_path, PathSafetyError,
};

// ── write_file ───────────────────────────────────────────────────

/// `write_file` — UTF-8 text write with auto-mkdir for parents.
pub async fn handle_write_file(payload: Value) -> Value {
    let path_input = payload.get("path").and_then(Value::as_str).unwrap_or("");
    let cwd_input = payload.get("cwd").and_then(Value::as_str);
    let content = payload
        .get("content")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();

    let resolved = match resolve_safe_dir(path_input, cwd_input) {
        Ok(p) => p,
        Err(e) => return error_response(format_path_error(e)),
    };

    let bytes = content.into_bytes();
    if bytes.len() > MAX_FILE_BYTES {
        return error_response(format!(
            "Content too large: {} bytes (max {} MB)",
            bytes.len(),
            MAX_FILE_BYTES / 1024 / 1024
        ));
    }

    let resolved_for_blocking = resolved.clone();
    spawn_blocking_or_err(move || write_blocking(&resolved_for_blocking, &bytes)).await
}

fn write_blocking(path: &Path, data: &[u8]) -> Value {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            if let Err(e) = std::fs::create_dir_all(parent) {
                return io_error_response(path, e);
            }
        }
    }
    if let Err(e) = std::fs::write(path, data) {
        return io_error_response(path, e);
    }
    json!({
        "success": true,
        "bytes_written": data.len(),
        "path": path.to_string_lossy(),
    })
}

// ── mkdir ────────────────────────────────────────────────────────

/// `mkdir` — create a directory (parents=true by default).
pub async fn handle_mkdir(payload: Value) -> Value {
    let path_input = payload.get("path").and_then(Value::as_str).unwrap_or("");
    let cwd_input = payload.get("cwd").and_then(Value::as_str);
    let parents = payload
        .get("parents")
        .and_then(Value::as_bool)
        .unwrap_or(true);

    let resolved = match resolve_safe_dir(path_input, cwd_input) {
        Ok(p) => p,
        Err(e) => return error_response(format_path_error(e)),
    };
    let resolved_for_blocking = resolved.clone();
    spawn_blocking_or_err(move || mkdir_blocking(&resolved_for_blocking, parents))
        .await
}

fn mkdir_blocking(path: &Path, parents: bool) -> Value {
    let exists_already = path.exists();
    let result = if parents {
        std::fs::create_dir_all(path)
    } else {
        std::fs::create_dir(path)
    };
    match result {
        Ok(()) => json!({
            "success": true,
            "path": path.to_string_lossy(),
            "created": !exists_already,
        }),
        Err(e) => {
            if e.kind() == std::io::ErrorKind::AlreadyExists {
                if parents {
                    return json!({
                        "success": true,
                        "path": path.to_string_lossy(),
                        "created": false,
                    });
                }
                return error_response(format!(
                    "Already exists: {}",
                    path.display()
                ));
            }
            io_error_response(path, e)
        }
    }
}

// ── delete ───────────────────────────────────────────────────────

/// `delete` — remove a file or (with recursive=true) a directory.
/// Refuses to delete the workspace root itself.
pub async fn handle_delete(payload: Value) -> Value {
    let path_input = payload.get("path").and_then(Value::as_str).unwrap_or("");
    let cwd_input = payload.get("cwd").and_then(Value::as_str);
    let recursive = payload
        .get("recursive")
        .and_then(Value::as_bool)
        .unwrap_or(false);

    let resolved = match resolve_safe_path(path_input, cwd_input) {
        Ok(p) => p,
        Err(e) => return error_response(format_path_error(e)),
    };

    // Refuse to delete the canonicalised workspace root.
    if let Some(cwd) = cwd_input {
        if let Ok(base) = dunce::canonicalize(cwd) {
            if resolved == base {
                return error_response("Refusing to delete workspace root");
            }
        }
    }

    let resolved_for_blocking = resolved.clone();
    spawn_blocking_or_err(move || delete_blocking(&resolved_for_blocking, recursive))
        .await
}

fn delete_blocking(path: &Path, recursive: bool) -> Value {
    let meta = match std::fs::symlink_metadata(path) {
        Ok(m) => m,
        Err(e) => {
            if e.kind() == std::io::ErrorKind::NotFound {
                return error_response(format!("Path not found: {}", path.display()));
            }
            return io_error_response(path, e);
        }
    };
    let ftype = meta.file_type();
    if ftype.is_symlink() || ftype.is_file() {
        if let Err(e) = std::fs::remove_file(path) {
            return io_error_response(path, e);
        }
        return json!({
            "success": true,
            "path": path.to_string_lossy(),
            "type": "file",
        });
    }
    if ftype.is_dir() {
        if !recursive {
            return error_response("Directory delete requires recursive=True");
        }
        if let Err(e) = std::fs::remove_dir_all(path) {
            return io_error_response(path, e);
        }
        return json!({
            "success": true,
            "path": path.to_string_lossy(),
            "type": "directory",
        });
    }
    error_response(format!("Path not found: {}", path.display()))
}

// ── move ─────────────────────────────────────────────────────────

/// `move` — relocate a file/dir. With overwrite=true, removes any
/// existing destination first (matches Python's `shutil.move`).
pub async fn handle_move(payload: Value) -> Value {
    let src_input = payload.get("src").and_then(Value::as_str).unwrap_or("");
    let dst_input = payload.get("dst").and_then(Value::as_str).unwrap_or("");
    let cwd_input = payload.get("cwd").and_then(Value::as_str);
    let overwrite = payload
        .get("overwrite")
        .and_then(Value::as_bool)
        .unwrap_or(false);

    let src = match resolve_safe_path(src_input, cwd_input) {
        Ok(p) => p,
        Err(e) => return error_response(format_path_error(e)),
    };
    let dst = match resolve_safe_dir(dst_input, cwd_input) {
        Ok(p) => p,
        Err(e) => return error_response(format_path_error(e)),
    };
    spawn_blocking_or_err(move || move_blocking(src, dst, overwrite)).await
}

fn move_blocking(src: std::path::PathBuf, dst: std::path::PathBuf, overwrite: bool) -> Value {
    if !src.exists() && std::fs::symlink_metadata(&src).is_err() {
        return error_response(format!("Source not found: {}", src.display()));
    }
    if dst.exists() {
        if !overwrite {
            return error_response(format!("Destination exists: {}", dst.display()));
        }
        let dst_meta = match std::fs::symlink_metadata(&dst) {
            Ok(m) => m,
            Err(e) => return io_error_response(&dst, e),
        };
        if dst_meta.is_dir() && !dst_meta.file_type().is_symlink() {
            if let Err(e) = std::fs::remove_dir_all(&dst) {
                return io_error_response(&dst, e);
            }
        } else if let Err(e) = std::fs::remove_file(&dst) {
            return io_error_response(&dst, e);
        }
    }
    if let Some(parent) = dst.parent() {
        if !parent.as_os_str().is_empty() {
            if let Err(e) = std::fs::create_dir_all(parent) {
                return io_error_response(parent, e);
            }
        }
    }
    // `rename` is the fast path. Falls back to copy+remove on
    // cross-device move (Linux EXDEV / Windows different volume).
    match std::fs::rename(&src, &dst) {
        Ok(()) => json!({
            "success": true,
            "src": src.to_string_lossy(),
            "dst": dst.to_string_lossy(),
        }),
        Err(e) if e.raw_os_error() == Some(libc_exdev()) => {
            if let Err(e) = copy_recursive(&src, &dst) {
                return io_error_response(&dst, e);
            }
            let cleanup = if src.is_dir() {
                std::fs::remove_dir_all(&src)
            } else {
                std::fs::remove_file(&src)
            };
            if let Err(e) = cleanup {
                return io_error_response(&src, e);
            }
            json!({
                "success": true,
                "src": src.to_string_lossy(),
                "dst": dst.to_string_lossy(),
            })
        }
        Err(e) => io_error_response(&dst, e),
    }
}

#[cfg(unix)]
fn libc_exdev() -> i32 {
    18 // EXDEV on Linux/macOS
}
#[cfg(windows)]
fn libc_exdev() -> i32 {
    17 // ERROR_NOT_SAME_DEVICE on Windows
}

// ── copy ─────────────────────────────────────────────────────────

/// `copy` — recursively duplicate a file or directory. With
/// overwrite=true, replaces existing destination (matches Python's
/// `shutil.copytree`).
pub async fn handle_copy(payload: Value) -> Value {
    let src_input = payload.get("src").and_then(Value::as_str).unwrap_or("");
    let dst_input = payload.get("dst").and_then(Value::as_str).unwrap_or("");
    let cwd_input = payload.get("cwd").and_then(Value::as_str);
    let overwrite = payload
        .get("overwrite")
        .and_then(Value::as_bool)
        .unwrap_or(false);

    let src = match resolve_safe_path(src_input, cwd_input) {
        Ok(p) => p,
        Err(e) => return error_response(format_path_error(e)),
    };
    let dst = match resolve_safe_dir(dst_input, cwd_input) {
        Ok(p) => p,
        Err(e) => return error_response(format_path_error(e)),
    };
    spawn_blocking_or_err(move || copy_blocking(src, dst, overwrite)).await
}

fn copy_blocking(src: std::path::PathBuf, dst: std::path::PathBuf, overwrite: bool) -> Value {
    if !src.exists() && std::fs::symlink_metadata(&src).is_err() {
        return error_response(format!("Source not found: {}", src.display()));
    }
    if dst.exists() && !overwrite {
        return error_response(format!("Destination exists: {}", dst.display()));
    }
    if let Some(parent) = dst.parent() {
        if !parent.as_os_str().is_empty() {
            if let Err(e) = std::fs::create_dir_all(parent) {
                return io_error_response(parent, e);
            }
        }
    }
    let src_meta = match std::fs::symlink_metadata(&src) {
        Ok(m) => m,
        Err(e) => return io_error_response(&src, e),
    };
    if src_meta.is_dir() && !src_meta.file_type().is_symlink() {
        if dst.exists() {
            if let Err(e) = std::fs::remove_dir_all(&dst) {
                return io_error_response(&dst, e);
            }
        }
        if let Err(e) = copy_recursive(&src, &dst) {
            return io_error_response(&dst, e);
        }
    } else if let Err(e) = std::fs::copy(&src, &dst) {
        return io_error_response(&dst, e);
    }
    json!({
        "success": true,
        "src": src.to_string_lossy(),
        "dst": dst.to_string_lossy(),
    })
}

/// Manual recursive copy (no stdlib equivalent of `shutil.copytree`).
/// Symlinks are dereferenced — matches `shutil.copytree` default.
fn copy_recursive(src: &Path, dst: &Path) -> std::io::Result<()> {
    let meta = std::fs::symlink_metadata(src)?;
    if meta.is_dir() && !meta.file_type().is_symlink() {
        std::fs::create_dir_all(dst)?;
        for entry in std::fs::read_dir(src)? {
            let entry = entry?;
            copy_recursive(&entry.path(), &dst.join(entry.file_name()))?;
        }
        Ok(())
    } else {
        std::fs::copy(src, dst).map(|_| ())
    }
}

// ── helpers ──────────────────────────────────────────────────────

async fn spawn_blocking_or_err<F: FnOnce() -> Value + Send + 'static>(f: F) -> Value {
    match tokio::task::spawn_blocking(f).await {
        Ok(v) => v,
        Err(e) => error_response(format!("blocking task panicked: {e}")),
    }
}

fn error_response(msg: impl Into<String>) -> Value {
    json!({
        "success": false,
        "error": msg.into(),
    })
}

fn io_error_response(path: &Path, e: std::io::Error) -> Value {
    use std::io::ErrorKind;
    match e.kind() {
        ErrorKind::PermissionDenied => {
            error_response(format!("Permission denied: {}", path.display()))
        }
        ErrorKind::NotFound => {
            error_response(format!("Path not found: {}", path.display()))
        }
        _ => error_response(e.to_string()),
    }
}

fn format_path_error(e: PathSafetyError) -> String {
    match e {
        PathSafetyError::CwdRequired => "cwd is required".into(),
        PathSafetyError::CwdNotADir(c) => format!("Working directory does not exist: {c}"),
        PathSafetyError::NulByte => "Invalid path: contains NUL byte".into(),
        PathSafetyError::Traversal => "Path traversal not allowed".into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    fn tmp() -> TempDir {
        tempfile::tempdir().unwrap()
    }
    fn cwd_str(d: &TempDir) -> String {
        d.path().to_string_lossy().into_owned()
    }

    // ── write_file ───────────────────────────────────────────────

    #[tokio::test]
    async fn write_basic() {
        let d = tmp();
        let v = handle_write_file(
            json!({"path": "out.txt", "cwd": cwd_str(&d), "content": "hello"}),
        )
        .await;
        assert_eq!(v["success"], true);
        assert_eq!(v["bytes_written"], 5);
        assert_eq!(fs::read_to_string(d.path().join("out.txt")).unwrap(), "hello");
    }

    #[tokio::test]
    async fn write_creates_parent_dirs() {
        let d = tmp();
        let v = handle_write_file(json!({
            "path": "sub/nested/out.txt",
            "cwd": cwd_str(&d),
            "content": "x",
        }))
        .await;
        assert_eq!(v["success"], true);
        assert!(d.path().join("sub").join("nested").join("out.txt").exists());
    }

    #[tokio::test]
    async fn write_traversal_rejected() {
        let d = tmp();
        let v = handle_write_file(json!({
            "path": "../escape.txt",
            "cwd": cwd_str(&d),
            "content": "x",
        }))
        .await;
        assert_eq!(v["success"], false);
        assert!(v["error"]
            .as_str()
            .unwrap_or("")
            .to_lowercase()
            .contains("traversal"));
    }

    #[tokio::test]
    async fn write_missing_cwd_rejected() {
        let v = handle_write_file(json!({"path": "x", "content": "y"})).await;
        assert_eq!(v["success"], false);
        assert!(v["error"]
            .as_str()
            .unwrap_or("")
            .to_lowercase()
            .contains("cwd"));
    }

    #[tokio::test]
    async fn write_too_large_rejected() {
        let d = tmp();
        let big: String = std::iter::repeat('x')
            .take(MAX_FILE_BYTES + 1)
            .collect();
        let v = handle_write_file(json!({
            "path": "big.txt",
            "cwd": cwd_str(&d),
            "content": big,
        }))
        .await;
        assert_eq!(v["success"], false);
        assert!(v["error"]
            .as_str()
            .unwrap_or("")
            .contains("Content too large"));
    }

    // ── mkdir ────────────────────────────────────────────────────

    #[tokio::test]
    async fn mkdir_creates_new_dir() {
        let d = tmp();
        let v = handle_mkdir(json!({"path": "newdir", "cwd": cwd_str(&d)})).await;
        assert_eq!(v["success"], true);
        assert_eq!(v["created"], true);
        assert!(d.path().join("newdir").is_dir());
    }

    #[tokio::test]
    async fn mkdir_parents_default_true() {
        let d = tmp();
        let v = handle_mkdir(json!({
            "path": "a/b/c",
            "cwd": cwd_str(&d),
        }))
        .await;
        assert_eq!(v["success"], true);
        assert!(d.path().join("a").join("b").join("c").is_dir());
    }

    #[tokio::test]
    async fn mkdir_existing_with_parents_idempotent() {
        let d = tmp();
        fs::create_dir(d.path().join("exists")).unwrap();
        let v = handle_mkdir(json!({"path": "exists", "cwd": cwd_str(&d)})).await;
        assert_eq!(v["success"], true);
        assert_eq!(v["created"], false);
    }

    #[tokio::test]
    async fn mkdir_no_parents_existing_fails() {
        let d = tmp();
        fs::create_dir(d.path().join("exists")).unwrap();
        let v = handle_mkdir(json!({
            "path": "exists",
            "cwd": cwd_str(&d),
            "parents": false,
        }))
        .await;
        assert_eq!(v["success"], false);
        assert!(v["error"]
            .as_str()
            .unwrap_or("")
            .contains("Already exists"));
    }

    #[tokio::test]
    async fn mkdir_traversal_rejected() {
        let d = tmp();
        let v =
            handle_mkdir(json!({"path": "../escape", "cwd": cwd_str(&d)})).await;
        assert_eq!(v["success"], false);
        assert!(v["error"]
            .as_str()
            .unwrap_or("")
            .to_lowercase()
            .contains("traversal"));
    }

    // ── delete ───────────────────────────────────────────────────

    #[tokio::test]
    async fn delete_file() {
        let d = tmp();
        fs::write(d.path().join("victim.txt"), "x").unwrap();
        let v = handle_delete(json!({
            "path": "victim.txt",
            "cwd": cwd_str(&d),
        }))
        .await;
        assert_eq!(v["success"], true);
        assert_eq!(v["type"], "file");
        assert!(!d.path().join("victim.txt").exists());
    }

    #[tokio::test]
    async fn delete_dir_requires_recursive() {
        let d = tmp();
        fs::create_dir(d.path().join("victim_dir")).unwrap();
        fs::write(d.path().join("victim_dir").join("inside.txt"), "y").unwrap();
        let v = handle_delete(json!({
            "path": "victim_dir",
            "cwd": cwd_str(&d),
        }))
        .await;
        assert_eq!(v["success"], false);
        assert!(v["error"]
            .as_str()
            .unwrap_or("")
            .contains("recursive"));
        assert!(d.path().join("victim_dir").exists());
    }

    #[tokio::test]
    async fn delete_dir_recursive() {
        let d = tmp();
        fs::create_dir(d.path().join("victim_dir")).unwrap();
        fs::write(d.path().join("victim_dir").join("inside.txt"), "y").unwrap();
        let v = handle_delete(json!({
            "path": "victim_dir",
            "cwd": cwd_str(&d),
            "recursive": true,
        }))
        .await;
        assert_eq!(v["success"], true);
        assert_eq!(v["type"], "directory");
        assert!(!d.path().join("victim_dir").exists());
    }

    #[tokio::test]
    async fn delete_workspace_root_refused() {
        let d = tmp();
        let v = handle_delete(json!({
            "path": ".",
            "cwd": cwd_str(&d),
            "recursive": true,
        }))
        .await;
        assert_eq!(v["success"], false);
        assert!(v["error"]
            .as_str()
            .unwrap_or("")
            .contains("workspace root"));
        assert!(d.path().exists());
    }

    // ── move ─────────────────────────────────────────────────────

    #[tokio::test]
    async fn move_file_basic() {
        let d = tmp();
        fs::write(d.path().join("a.txt"), "x").unwrap();
        let v = handle_move(json!({
            "src": "a.txt",
            "dst": "b.txt",
            "cwd": cwd_str(&d),
        }))
        .await;
        assert_eq!(v["success"], true);
        assert!(!d.path().join("a.txt").exists());
        assert!(d.path().join("b.txt").exists());
    }

    #[tokio::test]
    async fn move_dst_exists_no_overwrite_fails() {
        let d = tmp();
        fs::write(d.path().join("a.txt"), "x").unwrap();
        fs::write(d.path().join("b.txt"), "y").unwrap();
        let v = handle_move(json!({
            "src": "a.txt",
            "dst": "b.txt",
            "cwd": cwd_str(&d),
        }))
        .await;
        assert_eq!(v["success"], false);
        assert!(v["error"]
            .as_str()
            .unwrap_or("")
            .contains("Destination exists"));
    }

    #[tokio::test]
    async fn move_overwrite() {
        let d = tmp();
        fs::write(d.path().join("a.txt"), "from-a").unwrap();
        fs::write(d.path().join("b.txt"), "from-b").unwrap();
        let v = handle_move(json!({
            "src": "a.txt",
            "dst": "b.txt",
            "cwd": cwd_str(&d),
            "overwrite": true,
        }))
        .await;
        assert_eq!(v["success"], true);
        assert_eq!(fs::read_to_string(d.path().join("b.txt")).unwrap(), "from-a");
    }

    #[tokio::test]
    async fn move_creates_parent_dir() {
        let d = tmp();
        fs::write(d.path().join("a.txt"), "x").unwrap();
        let v = handle_move(json!({
            "src": "a.txt",
            "dst": "newsub/b.txt",
            "cwd": cwd_str(&d),
        }))
        .await;
        assert_eq!(v["success"], true);
        assert!(d.path().join("newsub").join("b.txt").exists());
    }

    #[tokio::test]
    async fn move_missing_src_rejected() {
        let d = tmp();
        let v = handle_move(json!({
            "src": "nope.txt",
            "dst": "x.txt",
            "cwd": cwd_str(&d),
        }))
        .await;
        // resolve_safe_path on a non-existent src fails first → traversal
        assert_eq!(v["success"], false);
        let err = v["error"].as_str().unwrap_or("").to_lowercase();
        assert!(
            err.contains("traversal") || err.contains("not found"),
            "err={err}"
        );
    }

    // ── copy ─────────────────────────────────────────────────────

    #[tokio::test]
    async fn copy_file_basic() {
        let d = tmp();
        fs::write(d.path().join("a.txt"), "data").unwrap();
        let v = handle_copy(json!({
            "src": "a.txt",
            "dst": "b.txt",
            "cwd": cwd_str(&d),
        }))
        .await;
        assert_eq!(v["success"], true);
        assert_eq!(fs::read_to_string(d.path().join("a.txt")).unwrap(), "data");
        assert_eq!(fs::read_to_string(d.path().join("b.txt")).unwrap(), "data");
    }

    #[tokio::test]
    async fn copy_dir_recursive() {
        let d = tmp();
        fs::create_dir(d.path().join("src_dir")).unwrap();
        fs::write(d.path().join("src_dir").join("a.txt"), "1").unwrap();
        fs::create_dir(d.path().join("src_dir").join("nested")).unwrap();
        fs::write(d.path().join("src_dir").join("nested").join("b.txt"), "2").unwrap();
        let v = handle_copy(json!({
            "src": "src_dir",
            "dst": "dst_dir",
            "cwd": cwd_str(&d),
        }))
        .await;
        assert_eq!(v["success"], true);
        assert_eq!(
            fs::read_to_string(d.path().join("dst_dir").join("a.txt")).unwrap(),
            "1"
        );
        assert_eq!(
            fs::read_to_string(d.path().join("dst_dir").join("nested").join("b.txt")).unwrap(),
            "2"
        );
    }

    #[tokio::test]
    async fn copy_dst_exists_no_overwrite_fails() {
        let d = tmp();
        fs::write(d.path().join("a.txt"), "x").unwrap();
        fs::write(d.path().join("b.txt"), "y").unwrap();
        let v = handle_copy(json!({
            "src": "a.txt",
            "dst": "b.txt",
            "cwd": cwd_str(&d),
        }))
        .await;
        assert_eq!(v["success"], false);
        assert!(v["error"]
            .as_str()
            .unwrap_or("")
            .contains("Destination exists"));
    }

    #[tokio::test]
    async fn copy_overwrite_dir() {
        let d = tmp();
        fs::create_dir(d.path().join("src_dir")).unwrap();
        fs::write(d.path().join("src_dir").join("new.txt"), "new").unwrap();
        fs::create_dir(d.path().join("dst_dir")).unwrap();
        fs::write(d.path().join("dst_dir").join("old.txt"), "old").unwrap();
        let v = handle_copy(json!({
            "src": "src_dir",
            "dst": "dst_dir",
            "cwd": cwd_str(&d),
            "overwrite": true,
        }))
        .await;
        assert_eq!(v["success"], true);
        assert!(d.path().join("dst_dir").join("new.txt").exists());
        assert!(!d.path().join("dst_dir").join("old.txt").exists());
    }
}
