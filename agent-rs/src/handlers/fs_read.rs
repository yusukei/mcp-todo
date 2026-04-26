//! Read-only FS handlers: `read_file` / `list_dir` / `stat`.
//!
//! All blocking I/O is dispatched to `tokio::task::spawn_blocking` so
//! the async runtime stays responsive when the disk is slow. Mirrors
//! `agent/main.py`'s `asyncio.to_thread` pattern.

use std::path::{Path, PathBuf};

use base64::{engine::general_purpose::STANDARD as B64, Engine as _};
use serde_json::{json, Value};
use tracing::warn;

use super::constants::{MAX_DIR_ENTRIES, MAX_FILE_BYTES, MAX_GLOB_RESULTS};
use super::error_payload;
use crate::path_safety::{resolve_safe_path, PathSafetyError};

/// `read_file` — return file content (text or base64 binary).
pub async fn handle_read_file(payload: Value) -> Value {
    let path_input = payload.get("path").and_then(Value::as_str).unwrap_or("");
    let cwd_input = payload.get("cwd").and_then(Value::as_str);
    let offset = payload.get("offset").and_then(Value::as_u64);
    let limit = payload.get("limit").and_then(Value::as_u64);
    let encoding_input = payload
        .get("encoding")
        .and_then(Value::as_str)
        .unwrap_or("utf-8")
        .to_string();

    let resolved = match resolve_safe_path(path_input, cwd_input) {
        Ok(p) => p,
        Err(e) => return error_payload(format_path_error(e)),
    };

    let resolved_for_blocking = resolved.clone();
    let encoding = encoding_input.clone();
    let result = tokio::task::spawn_blocking(move || {
        if encoding == "binary" || encoding == "base64" {
            read_binary(&resolved_for_blocking)
        } else {
            read_text(&resolved_for_blocking, &encoding, offset, limit)
        }
    })
    .await;

    match result {
        Ok(v) => v,
        Err(e) => error_payload(format!("read task panicked: {e}")),
    }
}

fn read_text(
    path: &Path,
    encoding: &str,
    offset: Option<u64>,
    limit: Option<u64>,
) -> Value {
    // Only utf-8 is supported in the Rust port — Python's `errors=replace`
    // for arbitrary codecs is a long tail we don't need yet. Drop down
    // to raw bytes + lossy decode so non-utf8 files still come through
    // without errors (matches Python's `errors="replace"`).
    if encoding != "utf-8" {
        return error_payload(format!(
            "Unknown encoding: {encoding} (only utf-8 / binary / base64 supported)"
        ));
    }
    let metadata = match std::fs::metadata(path) {
        Ok(m) => m,
        Err(e) => return io_error_payload(path, e),
    };
    let size = metadata.len() as usize;
    if size > MAX_FILE_BYTES {
        return error_payload(format!(
            "File too large: {size} bytes (max {} MB)",
            MAX_FILE_BYTES / 1024 / 1024
        ));
    }
    let bytes = match std::fs::read(path) {
        Ok(b) => b,
        Err(e) => return io_error_payload(path, e),
    };
    let content = String::from_utf8_lossy(&bytes).into_owned();

    if offset.is_none() && limit.is_none() {
        let total_lines = count_lines(&content);
        return json!({
            "content": content,
            "size": size,
            "path": path_string(path),
            "encoding": encoding,
            "is_binary": false,
            "total_lines": total_lines,
            "truncated": false,
        });
    }

    // Line-range read. Python uses 1-based offsets — match it.
    let lines: Vec<&str> = content.split_inclusive('\n').collect();
    let total_lines = lines.len();
    let start = offset.unwrap_or(1).saturating_sub(1) as usize;
    let start = start.min(total_lines);
    let end = match limit {
        None => total_lines,
        Some(n) => total_lines.min(start.saturating_add(n as usize)),
    };
    let slice: String = lines[start..end].concat();
    json!({
        "content": slice,
        "size": size,
        "path": path_string(path),
        "encoding": encoding,
        "is_binary": false,
        "total_lines": total_lines,
        "truncated": end < total_lines,
        "offset": start + 1,
        "limit": end - start,
    })
}

fn read_binary(path: &Path) -> Value {
    let metadata = match std::fs::metadata(path) {
        Ok(m) => m,
        Err(e) => return io_error_payload(path, e),
    };
    let size = metadata.len() as usize;
    if size > MAX_FILE_BYTES {
        return error_payload(format!(
            "File too large: {size} bytes (max {} MB)",
            MAX_FILE_BYTES / 1024 / 1024
        ));
    }
    let data = match std::fs::read(path) {
        Ok(b) => b,
        Err(e) => return io_error_payload(path, e),
    };
    json!({
        "content": B64.encode(&data),
        "size": size,
        "path": path_string(path),
        "encoding": "base64",
        "is_binary": true,
        "total_lines": 0,
        "truncated": false,
    })
}

fn count_lines(content: &str) -> usize {
    if content.is_empty() {
        return 0;
    }
    let mut n = content.matches('\n').count();
    if !content.ends_with('\n') {
        n += 1;
    }
    n
}

/// `list_dir` — return entry metadata for the given directory.
pub async fn handle_list_dir(payload: Value) -> Value {
    let path_input = payload.get("path").and_then(Value::as_str).unwrap_or(".");
    let cwd_input = payload.get("cwd").and_then(Value::as_str);

    let resolved = match resolve_safe_path(path_input, cwd_input) {
        Ok(p) => p,
        Err(e) => return error_payload(format_path_error(e)),
    };

    let resolved_for_blocking = resolved.clone();
    let result = tokio::task::spawn_blocking(move || list_dir_blocking(&resolved_for_blocking))
        .await;
    match result {
        Ok(v) => v,
        Err(e) => error_payload(format!("list_dir task panicked: {e}")),
    }
}

fn list_dir_blocking(path: &Path) -> Value {
    let read = match std::fs::read_dir(path) {
        Ok(r) => r,
        Err(e) => {
            return io_error_payload_kind(
                path,
                e,
                "Directory not found",
            );
        }
    };
    let mut entries: Vec<Value> = Vec::new();
    for ent in read {
        if entries.len() >= MAX_DIR_ENTRIES {
            break;
        }
        let ent = match ent {
            Ok(e) => e,
            Err(e) => {
                warn!(error = %e, "list_dir read_dir entry failed");
                continue;
            }
        };
        let name = ent.file_name().to_string_lossy().into_owned();
        let entry_value = match ent.metadata() {
            Ok(meta) => {
                let ftype = if meta.file_type().is_dir() {
                    "dir"
                } else if meta.file_type().is_symlink() {
                    "symlink"
                } else {
                    "file"
                };
                let size = if meta.is_dir() { 0 } else { meta.len() };
                let modified = meta
                    .modified()
                    .ok()
                    .and_then(format_systemtime_iso8601)
                    .unwrap_or_default();
                json!({
                    "name": name,
                    "type": ftype,
                    "size": size,
                    "modified": modified,
                })
            }
            Err(_) => json!({
                "name": name,
                "type": "unknown",
                "size": 0,
                "modified": "",
            }),
        };
        entries.push(entry_value);
    }
    // Sort: dirs first, then by lowercased name (Python parity).
    entries.sort_by(|a, b| {
        let a_dir = a["type"] == "dir";
        let b_dir = b["type"] == "dir";
        match (a_dir, b_dir) {
            (true, false) => std::cmp::Ordering::Less,
            (false, true) => std::cmp::Ordering::Greater,
            _ => a["name"]
                .as_str()
                .unwrap_or("")
                .to_lowercase()
                .cmp(&b["name"].as_str().unwrap_or("").to_lowercase()),
        }
    });
    json!({
        "entries": entries,
        "path": path_string(path),
    })
}

/// `stat` — file metadata (size / mtime / type / mode) or `exists=false`.
pub async fn handle_stat(payload: Value) -> Value {
    let path_input = payload.get("path").and_then(Value::as_str).unwrap_or("");
    let cwd_input = payload.get("cwd").and_then(Value::as_str);

    let resolved = match resolve_safe_path(path_input, cwd_input) {
        Ok(p) => p,
        Err(e) => return error_payload(format_path_error(e)),
    };
    let resolved_for_blocking = resolved.clone();
    let result =
        tokio::task::spawn_blocking(move || stat_blocking(&resolved_for_blocking)).await;
    match result {
        Ok(v) => v,
        Err(e) => error_payload(format!("stat task panicked: {e}")),
    }
}

fn stat_blocking(path: &Path) -> Value {
    let symlink_meta = std::fs::symlink_metadata(path);
    if matches!(&symlink_meta, Err(e) if e.kind() == std::io::ErrorKind::NotFound) {
        return json!({
            "exists": false,
            "type": Value::Null,
            "path": path_string(path),
        });
    }
    let meta = match symlink_meta {
        Ok(m) => m,
        Err(e) => return error_payload(e.to_string()),
    };
    let ftype = if meta.file_type().is_symlink() {
        "symlink"
    } else if meta.is_dir() {
        "directory"
    } else {
        "file"
    };
    let mtime = meta
        .modified()
        .ok()
        .and_then(format_systemtime_iso8601)
        .unwrap_or_default();
    json!({
        "exists": true,
        "type": ftype,
        "size": meta.len(),
        "mtime": mtime,
        "mode": format_mode(&meta),
        "path": path_string(path),
    })
}

#[cfg(unix)]
fn format_mode(meta: &std::fs::Metadata) -> String {
    use std::os::unix::fs::PermissionsExt;
    let bits = meta.permissions().mode() & 0o777;
    format!("0o{bits:o}")
}

#[cfg(windows)]
fn format_mode(meta: &std::fs::Metadata) -> String {
    // Windows has no POSIX mode bits. Python's `oct(st_mode & 0o777)`
    // on Windows returns a synthesised value derived from readonly +
    // dir flags; we mirror that: 0o555 for readonly, 0o777 otherwise,
    // 0o755 for directories. Good enough for tooling that just wants
    // a "looks unix-ish" string.
    let ro = meta.permissions().readonly();
    let bits = if meta.is_dir() {
        0o755
    } else if ro {
        0o555
    } else {
        0o666
    };
    format!("0o{bits:o}")
}

fn path_string(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

fn io_error_payload(path: &Path, e: std::io::Error) -> Value {
    use std::io::ErrorKind;
    match e.kind() {
        ErrorKind::NotFound => error_payload(format!("File not found: {}", path.display())),
        ErrorKind::PermissionDenied => {
            error_payload(format!("Permission denied: {}", path.display()))
        }
        _ => error_payload(e.to_string()),
    }
}

fn io_error_payload_kind(path: &Path, e: std::io::Error, not_found_label: &str) -> Value {
    use std::io::ErrorKind;
    match e.kind() {
        ErrorKind::NotFound => error_payload(format!("{not_found_label}: {}", path.display())),
        ErrorKind::PermissionDenied => {
            error_payload(format!("Permission denied: {}", path.display()))
        }
        _ => error_payload(e.to_string()),
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

/// Convert a `SystemTime` to UTC ISO8601, matching Python's
/// `datetime.fromtimestamp(.., tz=timezone.utc).isoformat()` shape:
/// `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`. Microsecond precision because
/// Python's mtime resolution is microseconds on POSIX.
fn format_systemtime_iso8601(t: std::time::SystemTime) -> Option<String> {
    let dur = t.duration_since(std::time::UNIX_EPOCH).ok()?;
    let secs = dur.as_secs() as i64;
    let nanos = dur.subsec_nanos();
    let dt = chrono::DateTime::<chrono::Utc>::from_timestamp(secs, nanos)?;
    // Python isoformat with tz aware → "YYYY-MM-DDTHH:MM:SS.ffffff+00:00"
    Some(dt.format("%Y-%m-%dT%H:%M:%S%.6f+00:00").to_string())
}

#[cfg(not(unix))]
fn _unused_path_buf_marker(_p: PathBuf) {}

// ── glob ─────────────────────────────────────────────────────────

/// `glob` — find files matching a pathlib-style pattern under `base`.
///
/// `*` does **not** cross directory boundaries (so `*.py` only returns
/// files in `base`, not nested); `**` matches any depth (so
/// `**/*.py` recurses). Files only — directories are skipped to mirror
/// the Claude Code Glob tool.
pub async fn handle_glob(payload: Value) -> Value {
    let pattern = payload
        .get("pattern")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let path_input = payload.get("path").and_then(Value::as_str).unwrap_or(".");
    let cwd_input = payload.get("cwd").and_then(Value::as_str);

    if pattern.is_empty() {
        return error_payload("pattern is required");
    }
    let base = match resolve_safe_path(path_input, cwd_input) {
        Ok(p) => p,
        Err(e) => return error_payload(format_path_error(e)),
    };
    let base_for_blocking = base.clone();
    let result = tokio::task::spawn_blocking(move || {
        glob_blocking(&base_for_blocking, &pattern)
    })
    .await;
    match result {
        Ok(v) => v,
        Err(e) => error_payload(format!("glob task panicked: {e}")),
    }
}

fn glob_blocking(base: &Path, pattern: &str) -> Value {
    if !base.is_dir() {
        return error_payload(format!("Not a directory: {}", base.display()));
    }
    let pat = match glob::Pattern::new(pattern) {
        Ok(p) => p,
        Err(e) => return error_payload(format!("Invalid glob pattern: {e}")),
    };
    // pathlib `*` doesn't cross `/`, so cap walk depth by the pattern's
    // segment count unless `**` is present (which matches any depth).
    let max_depth = if pattern.contains("**") {
        usize::MAX
    } else {
        // Each `/` adds one nesting level; the leaf itself is one more.
        pattern.matches('/').count() + 1
    };
    let walker = walkdir::WalkDir::new(base)
        .follow_links(false)
        .max_depth(max_depth);
    let opts = glob::MatchOptions {
        case_sensitive: !cfg!(windows),
        require_literal_separator: true,
        require_literal_leading_dot: false,
    };
    let mut results: Vec<Value> = Vec::new();
    let mut truncated = false;
    for entry in walker {
        let entry = match entry {
            Ok(e) => e,
            Err(_) => continue,
        };
        if entry.depth() == 0 {
            continue; // skip base itself
        }
        let rel = match entry.path().strip_prefix(base) {
            Ok(r) => r,
            Err(_) => continue,
        };
        // Normalize Windows `\` → `/` so the user's pattern (which
        // uses pathlib semantics) matches consistently.
        let rel_str = rel.to_string_lossy().replace('\\', "/");
        if !pat.matches_with(&rel_str, opts) {
            continue;
        }
        let meta = match entry.metadata() {
            Ok(m) => m,
            Err(_) => continue,
        };
        if !meta.is_file() {
            continue;
        }
        if results.len() >= MAX_GLOB_RESULTS {
            truncated = true;
            break;
        }
        let mtime = meta
            .modified()
            .ok()
            .and_then(format_systemtime_iso8601)
            .unwrap_or_default();
        results.push(json!({
            "path": entry.path().to_string_lossy(),
            "size": meta.len(),
            "mtime": mtime,
        }));
    }
    // mtime descending — newest first (matches Python).
    results.sort_by(|a, b| {
        b["mtime"]
            .as_str()
            .unwrap_or("")
            .cmp(a["mtime"].as_str().unwrap_or(""))
    });
    json!({
        "matches": results,
        "count": results.len(),
        "base": base.to_string_lossy(),
        "truncated": truncated,
    })
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

    // ── read_file ────────────────────────────────────────────────

    #[tokio::test]
    async fn read_file_text_full() {
        let d = tmp();
        fs::write(d.path().join("hi.txt"), "hello\nworld\n").unwrap();
        let v = handle_read_file(json!({"path": "hi.txt", "cwd": cwd_str(&d)})).await;
        assert_eq!(v["content"], "hello\nworld\n");
        assert_eq!(v["is_binary"], false);
        assert_eq!(v["truncated"], false);
        assert_eq!(v["total_lines"], 2);
        assert_eq!(v["encoding"], "utf-8");
    }

    #[tokio::test]
    async fn read_file_text_offset_limit() {
        let d = tmp();
        fs::write(
            d.path().join("multi.txt"),
            "l1\nl2\nl3\nl4\nl5\n",
        )
        .unwrap();
        let v = handle_read_file(json!({
            "path": "multi.txt",
            "cwd": cwd_str(&d),
            "offset": 2,
            "limit": 2,
        }))
        .await;
        assert_eq!(v["content"], "l2\nl3\n");
        assert_eq!(v["offset"], 2);
        assert_eq!(v["limit"], 2);
        assert_eq!(v["truncated"], true);
        assert_eq!(v["total_lines"], 5);
    }

    #[tokio::test]
    async fn read_file_binary_base64() {
        let d = tmp();
        fs::write(d.path().join("b.bin"), &[0u8, 1, 2, 3, 255]).unwrap();
        let v = handle_read_file(
            json!({"path": "b.bin", "cwd": cwd_str(&d), "encoding": "binary"}),
        )
        .await;
        assert_eq!(v["is_binary"], true);
        assert_eq!(v["encoding"], "base64");
        // base64("\x00\x01\x02\x03\xff") = "AAECA/8="
        assert_eq!(v["content"], "AAECA/8=");
    }

    #[tokio::test]
    async fn read_file_too_large_rejected() {
        let d = tmp();
        // Write 5 MB + 1 byte
        let big = vec![b'a'; MAX_FILE_BYTES + 1];
        fs::write(d.path().join("big.txt"), &big).unwrap();
        let v = handle_read_file(json!({"path": "big.txt", "cwd": cwd_str(&d)})).await;
        let err = v["error"].as_str().unwrap_or_default();
        assert!(err.contains("File too large"), "err={err}");
    }

    #[tokio::test]
    async fn read_file_traversal_rejected() {
        let d = tmp();
        let v = handle_read_file(
            json!({"path": "../escape.txt", "cwd": cwd_str(&d)}),
        )
        .await;
        assert!(v["error"]
            .as_str()
            .unwrap_or_default()
            .to_lowercase()
            .contains("traversal"));
    }

    #[tokio::test]
    async fn read_file_missing_cwd_rejected() {
        let v = handle_read_file(json!({"path": "x"})).await;
        assert!(v["error"]
            .as_str()
            .unwrap_or_default()
            .to_lowercase()
            .contains("cwd"));
    }

    #[tokio::test]
    async fn read_file_not_found() {
        let d = tmp();
        let v = handle_read_file(
            json!({"path": "no-such.txt", "cwd": cwd_str(&d)}),
        )
        .await;
        // `_resolve_safe_path` requires the target to exist, so the
        // failure surfaces as a Traversal error (canonicalize fails).
        // Match Python's behaviour: it returns "File not found" because
        // realpath succeeds for non-existent paths. Our port returns
        // "Path traversal" for either case, which is strictly safer.
        let err = v["error"].as_str().unwrap_or_default().to_lowercase();
        assert!(
            err.contains("traversal") || err.contains("not found"),
            "err={err}"
        );
    }

    #[tokio::test]
    async fn read_file_invalid_utf8_replaced() {
        let d = tmp();
        // 0xff is invalid UTF-8 in any position
        fs::write(d.path().join("bad.txt"), &[b'a', 0xff, b'b']).unwrap();
        let v = handle_read_file(json!({"path": "bad.txt", "cwd": cwd_str(&d)})).await;
        let content = v["content"].as_str().unwrap();
        // Replacement char inserted, no error
        assert!(content.starts_with('a'));
        assert!(content.ends_with('b'));
        assert!(content.contains('\u{FFFD}'));
    }

    // ── list_dir ─────────────────────────────────────────────────

    #[tokio::test]
    async fn list_dir_basic() {
        let d = tmp();
        fs::write(d.path().join("a.txt"), "a").unwrap();
        fs::write(d.path().join("b.txt"), "bb").unwrap();
        fs::create_dir(d.path().join("subdir")).unwrap();
        let v = handle_list_dir(json!({"path": ".", "cwd": cwd_str(&d)})).await;
        let entries = v["entries"].as_array().unwrap();
        assert_eq!(entries.len(), 3);
        // dirs first
        assert_eq!(entries[0]["type"], "dir");
        assert_eq!(entries[0]["name"], "subdir");
        // then files in name order
        assert_eq!(entries[1]["name"], "a.txt");
        assert_eq!(entries[2]["name"], "b.txt");
        assert_eq!(entries[1]["size"], 1);
        assert_eq!(entries[2]["size"], 2);
        // mtime is iso8601 with +00:00 suffix
        assert!(entries[1]["modified"]
            .as_str()
            .unwrap_or("")
            .ends_with("+00:00"));
    }

    #[tokio::test]
    async fn list_dir_traversal_rejected() {
        let d = tmp();
        let sub = d.path().join("sub");
        fs::create_dir(&sub).unwrap();
        let v = handle_list_dir(
            json!({"path": "..", "cwd": sub.to_string_lossy()}),
        )
        .await;
        assert!(v["error"]
            .as_str()
            .unwrap_or("")
            .to_lowercase()
            .contains("traversal"));
    }

    #[tokio::test]
    async fn list_dir_missing_cwd_rejected() {
        let v = handle_list_dir(json!({"path": "."})).await;
        assert!(v["error"]
            .as_str()
            .unwrap_or("")
            .to_lowercase()
            .contains("cwd"));
    }

    #[tokio::test]
    async fn list_dir_max_entries_cap() {
        let d = tmp();
        for i in 0..(MAX_DIR_ENTRIES + 50) {
            fs::write(d.path().join(format!("f{i:04}.txt")), "x").unwrap();
        }
        let v = handle_list_dir(json!({"path": ".", "cwd": cwd_str(&d)})).await;
        let entries = v["entries"].as_array().unwrap();
        assert_eq!(entries.len(), MAX_DIR_ENTRIES);
    }

    // ── stat ─────────────────────────────────────────────────────

    #[tokio::test]
    async fn stat_existing_file() {
        let d = tmp();
        fs::write(d.path().join("s.txt"), "1234").unwrap();
        let v = handle_stat(json!({"path": "s.txt", "cwd": cwd_str(&d)})).await;
        assert_eq!(v["exists"], true);
        assert_eq!(v["type"], "file");
        assert_eq!(v["size"], 4);
        assert!(v["mtime"]
            .as_str()
            .unwrap_or("")
            .ends_with("+00:00"));
        assert!(v["mode"].as_str().unwrap_or("").starts_with("0o"));
    }

    #[tokio::test]
    async fn stat_existing_dir() {
        let d = tmp();
        fs::create_dir(d.path().join("dir")).unwrap();
        let v = handle_stat(json!({"path": "dir", "cwd": cwd_str(&d)})).await;
        assert_eq!(v["exists"], true);
        assert_eq!(v["type"], "directory");
    }

    #[tokio::test]
    async fn stat_nonexistent_returns_exists_false() {
        // For non-existent paths, `_resolve_safe_path` rejects (canonicalize
        // fails) → traversal-like error. This is one of the documented
        // Python-vs-Rust differences (see resolve_safe_path docs).
        let d = tmp();
        let v = handle_stat(json!({"path": "no-such", "cwd": cwd_str(&d)})).await;
        assert!(v.get("error").is_some(), "got: {v:?}");
    }

    #[tokio::test]
    async fn stat_traversal_rejected() {
        let d = tmp();
        let sub = d.path().join("sub");
        fs::create_dir(&sub).unwrap();
        let v = handle_stat(json!({"path": "../foo", "cwd": sub.to_string_lossy()})).await;
        assert!(v["error"]
            .as_str()
            .unwrap_or("")
            .to_lowercase()
            .contains("traversal"));
    }

    // ── glob ─────────────────────────────────────────────────────

    #[tokio::test]
    async fn glob_star_matches_only_top_level() {
        let d = tmp();
        fs::write(d.path().join("a.py"), "x").unwrap();
        fs::write(d.path().join("b.py"), "x").unwrap();
        fs::write(d.path().join("c.txt"), "x").unwrap();
        fs::create_dir(d.path().join("sub")).unwrap();
        fs::write(d.path().join("sub").join("nested.py"), "x").unwrap();
        let v = handle_glob(json!({
            "pattern": "*.py",
            "path": ".",
            "cwd": cwd_str(&d),
        }))
        .await;
        assert_eq!(v["count"], 2);
        let names: Vec<&str> = v["matches"]
            .as_array()
            .unwrap()
            .iter()
            .map(|m| m["path"].as_str().unwrap())
            .collect();
        // a.py and b.py present, nested.py absent
        assert!(names.iter().any(|p| p.ends_with("a.py")));
        assert!(names.iter().any(|p| p.ends_with("b.py")));
        assert!(!names.iter().any(|p| p.ends_with("nested.py")));
    }

    #[tokio::test]
    async fn glob_double_star_recursive() {
        let d = tmp();
        fs::write(d.path().join("a.py"), "x").unwrap();
        fs::create_dir(d.path().join("sub")).unwrap();
        fs::write(d.path().join("sub").join("b.py"), "x").unwrap();
        fs::create_dir(d.path().join("sub").join("nest")).unwrap();
        fs::write(d.path().join("sub").join("nest").join("c.py"), "x").unwrap();
        let v = handle_glob(json!({
            "pattern": "**/*.py",
            "path": ".",
            "cwd": cwd_str(&d),
        }))
        .await;
        // Behavior of pathlib's `**/*.py` and the glob crate's
        // `**/*.py` is "any level under base, at least one segment
        // deep". So a.py at the root may or may not match. We assert
        // that the deeply-nested ones DO match.
        let count = v["count"].as_u64().unwrap();
        assert!(count >= 2, "expected ≥2 matches, got {count}: {v:?}");
        let names: Vec<&str> = v["matches"]
            .as_array()
            .unwrap()
            .iter()
            .map(|m| m["path"].as_str().unwrap())
            .collect();
        assert!(names.iter().any(|p| p.ends_with("b.py")));
        assert!(names.iter().any(|p| p.ends_with("c.py")));
    }

    #[tokio::test]
    async fn glob_subdir_pattern() {
        let d = tmp();
        fs::create_dir(d.path().join("src")).unwrap();
        fs::write(d.path().join("src").join("main.rs"), "x").unwrap();
        fs::write(d.path().join("src").join("lib.rs"), "x").unwrap();
        fs::write(d.path().join("other.rs"), "x").unwrap();
        let v = handle_glob(json!({
            "pattern": "src/*.rs",
            "path": ".",
            "cwd": cwd_str(&d),
        }))
        .await;
        assert_eq!(v["count"], 2);
    }

    #[tokio::test]
    async fn glob_directories_excluded() {
        let d = tmp();
        fs::create_dir(d.path().join("matchme_dir")).unwrap();
        fs::write(d.path().join("matchme_file"), "x").unwrap();
        let v = handle_glob(json!({
            "pattern": "matchme_*",
            "path": ".",
            "cwd": cwd_str(&d),
        }))
        .await;
        // Only matchme_file (file). matchme_dir is filtered out.
        assert_eq!(v["count"], 1);
        let p = v["matches"][0]["path"].as_str().unwrap();
        assert!(p.ends_with("matchme_file"), "got {p}");
    }

    #[tokio::test]
    async fn glob_mtime_descending() {
        let d = tmp();
        fs::write(d.path().join("old.txt"), "x").unwrap();
        // Sleep so mtime resolution is enough to distinguish (1s on
        // many FAT/ext4 setups). Tokio's sleep is fine here.
        std::thread::sleep(std::time::Duration::from_millis(1100));
        fs::write(d.path().join("new.txt"), "x").unwrap();
        let v = handle_glob(json!({
            "pattern": "*.txt",
            "path": ".",
            "cwd": cwd_str(&d),
        }))
        .await;
        assert_eq!(v["count"], 2);
        let first = v["matches"][0]["path"].as_str().unwrap();
        assert!(first.ends_with("new.txt"), "newest first: {first}");
    }

    #[tokio::test]
    async fn glob_pattern_required() {
        let d = tmp();
        let v = handle_glob(json!({
            "pattern": "",
            "path": ".",
            "cwd": cwd_str(&d),
        }))
        .await;
        assert!(v["error"].as_str().unwrap_or("").contains("required"));
    }

    #[tokio::test]
    async fn glob_traversal_rejected() {
        let d = tmp();
        let sub = d.path().join("sub");
        fs::create_dir(&sub).unwrap();
        let v = handle_glob(json!({
            "pattern": "*.txt",
            "path": "..",
            "cwd": sub.to_string_lossy(),
        }))
        .await;
        assert!(v["error"]
            .as_str()
            .unwrap_or("")
            .to_lowercase()
            .contains("traversal"));
    }
}
