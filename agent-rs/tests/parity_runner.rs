//! Parity test runner — replay JSON fixtures against the Rust handlers
//! and assert each `expect` field matches.
//!
//! Run with `cargo test --test parity_runner`. Fixtures live under
//! `tests/parity/fixtures/{handler}/{name}.json` and use the format
//! documented in `tests/parity/README.md`.
//!
//! This file is a *thin* harness: the eventual Python `dump_fixtures.py`
//! plugin (see TODO in the README) will populate fixtures automatically;
//! until then they're hand-written.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use serde::Deserialize;
use serde_json::Value;
use tempfile::TempDir;

// `tests/` integration tests can't reach the binary's modules directly —
// re-import via the package's lib semantics by building a tiny shim.
// Easiest: re-derive what we need by calling the binary's public API
// through `mcp-workspace-agent-rs` only via process. But that's heavy
// for a unit-style runner.
//
// Better: drive each handler through its public dispatch entry. Since
// agent-rs is a `[[bin]]` crate, integration tests still see private
// items only via `pub use` re-exports in main.rs — we intentionally
// don't expose handler internals there. So instead we link in the
// handler crates as a separate `lib` target. For now, replicate the
// minimal dispatch surface here using a small subset of public crates.
//
// The pragmatic path for v0: shell out to the binary with a hand-rolled
// JSONL protocol. v1 (after the lib refactor) will call dispatch
// directly.
//
// In the meantime this runner runs *no* fixtures and asserts the
// fixture format parses. That's enough to keep CI green and the
// machinery healthy until the bin → lib refactor lands.

#[derive(Debug, Deserialize)]
struct Fixture {
    description: String,
    handler: String,
    #[serde(default)]
    setup: Vec<SetupStep>,
    input: Value,
    expect: Value,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum SetupStep {
    Write { path: String, content: String },
    Mkdir { path: String },
}

fn fixtures_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("parity")
        .join("fixtures")
}

fn load_all_fixtures() -> Vec<(PathBuf, Fixture)> {
    let mut out = Vec::new();
    walk_dir(&fixtures_dir(), &mut out);
    out
}

fn walk_dir(d: &Path, out: &mut Vec<(PathBuf, Fixture)>) {
    let Ok(read) = std::fs::read_dir(d) else {
        return;
    };
    for ent in read.flatten() {
        let p = ent.path();
        if p.is_dir() {
            walk_dir(&p, out);
        } else if p.extension().and_then(|s| s.to_str()) == Some("json") {
            let raw = std::fs::read_to_string(&p)
                .unwrap_or_else(|e| panic!("read {} failed: {e}", p.display()));
            let fix: Fixture = serde_json::from_str(&raw).unwrap_or_else(|e| {
                panic!("parse {} failed: {e}\n--- raw:\n{raw}", p.display())
            });
            out.push((p, fix));
        }
    }
}

fn apply_setup(d: &TempDir, steps: &[SetupStep]) {
    for step in steps {
        match step {
            SetupStep::Write { path, content } => {
                let full = d.path().join(path);
                if let Some(parent) = full.parent() {
                    std::fs::create_dir_all(parent).expect("create parent");
                }
                std::fs::write(&full, content).expect("write fixture file");
            }
            SetupStep::Mkdir { path } => {
                std::fs::create_dir_all(d.path().join(path)).expect("mkdir");
            }
        }
    }
}

/// Walk `expect` and `actual` in lock-step. Each field in `expect`
/// must be present in `actual`, with templates honoured:
/// - `"{{cwd}}"`        → must equal the canonicalised tempdir
/// - `"{{path:foo}}"`   → must equal the canonicalised `<tempdir>/foo`
/// - `"{{any}}"`        → just present
/// - `"{{any:int}}"`    → present and integer
/// - `"{{any:string}}"` → present and string
fn assert_match(expect: &Value, actual: &Value, cwd: &Path, path: &str) {
    match expect {
        Value::String(s) if s.starts_with("{{") && s.ends_with("}}") => {
            let inner = &s[2..s.len() - 2];
            match inner {
                "cwd" => {
                    let want = canon_str(cwd);
                    let got = actual.as_str().unwrap_or_default();
                    assert!(
                        paths_equal(&want, got),
                        "{path}: want cwd '{want}', got '{got}'"
                    );
                }
                t if t.starts_with("path:") => {
                    let rel = &t["path:".len()..];
                    let want = canon_str(&cwd.join(rel));
                    let got = actual.as_str().unwrap_or_default();
                    assert!(
                        paths_equal(&want, got),
                        "{path}: want path '{want}', got '{got}'"
                    );
                }
                "any" => {
                    // present (could be null too — accept anything that exists)
                    let _ = actual;
                }
                "any:int" => assert!(
                    actual.is_i64() || actual.is_u64(),
                    "{path}: expected integer, got {actual:?}"
                ),
                "any:string" => assert!(
                    actual.is_string(),
                    "{path}: expected string, got {actual:?}"
                ),
                other => panic!("{path}: unknown template {{{{{other}}}}}"),
            }
        }
        Value::Object(obj) => {
            let actual_obj = actual
                .as_object()
                .unwrap_or_else(|| panic!("{path}: expected object, got {actual:?}"));
            for (k, v) in obj {
                let next = actual_obj
                    .get(k)
                    .unwrap_or_else(|| panic!("{path}.{k}: missing in actual"));
                assert_match(v, next, cwd, &format!("{path}.{k}"));
            }
        }
        Value::Array(arr) => {
            let actual_arr = actual
                .as_array()
                .unwrap_or_else(|| panic!("{path}: expected array, got {actual:?}"));
            assert_eq!(
                arr.len(),
                actual_arr.len(),
                "{path}: array length mismatch"
            );
            for (i, (e, a)) in arr.iter().zip(actual_arr).enumerate() {
                assert_match(e, a, cwd, &format!("{path}[{i}]"));
            }
        }
        _ => assert_eq!(expect, actual, "{path}: value mismatch"),
    }
}

fn canon_str(p: &Path) -> String {
    dunce::canonicalize(p)
        .unwrap_or_else(|_| p.to_path_buf())
        .to_string_lossy()
        .into_owned()
}

fn paths_equal(want: &str, got: &str) -> bool {
    // Normalise separators for cross-platform fixture authoring.
    want.replace('\\', "/").to_lowercase() == got.replace('\\', "/").to_lowercase()
}

#[test]
fn all_fixtures_parse_cleanly() {
    let fixtures = load_all_fixtures();
    assert!(
        !fixtures.is_empty(),
        "no fixtures found under {:?}",
        fixtures_dir()
    );
    for (p, fix) in &fixtures {
        assert!(
            !fix.handler.is_empty(),
            "{} missing 'handler'",
            p.display()
        );
        assert!(
            !fix.description.is_empty(),
            "{} missing 'description'",
            p.display()
        );
        assert!(fix.input.is_object(), "{} input is not an object", p.display());
        assert!(
            fix.expect.is_object(),
            "{} expect is not an object",
            p.display()
        );
    }
    let count = fixtures.len();
    let by_handler: BTreeMap<&str, usize> = fixtures.iter().fold(
        BTreeMap::new(),
        |mut acc, (_, f)| {
            *acc.entry(f.handler.as_str()).or_insert(0) += 1;
            acc
        },
    );
    eprintln!("parity_runner: parsed {count} fixtures");
    for (h, c) in &by_handler {
        eprintln!("  {h}: {c}");
    }
}

/// Smoke test: assert_match's template engine handles the documented
/// shapes. Doesn't run any fixture — that's gated on the bin → lib
/// refactor (see module docs).
#[test]
fn assert_match_templates_work() {
    let cwd = tempfile::tempdir().unwrap();
    let cwd_path = cwd.path();

    // Arrange a fake actual that mimics what a handler would return.
    let actual_path = canon_str(&cwd_path.join("foo.txt"));
    let actual = serde_json::json!({
        "ok": true,
        "size": 42,
        "label": "hello",
        "path": actual_path,
        "cwd": canon_str(cwd_path),
    });
    let expect = serde_json::json!({
        "ok": true,
        "size": "{{any:int}}",
        "label": "{{any:string}}",
        "path": "{{path:foo.txt}}",
        "cwd": "{{cwd}}",
    });
    assert_match(&expect, &actual, cwd_path, "$");
}

#[test]
#[should_panic(expected = "value mismatch")]
fn assert_match_catches_value_mismatch() {
    let cwd = tempfile::tempdir().unwrap();
    let actual = serde_json::json!({"ok": false});
    let expect = serde_json::json!({"ok": true});
    assert_match(&expect, &actual, cwd.path(), "$");
}

#[test]
#[should_panic(expected = "missing in actual")]
fn assert_match_catches_missing_key() {
    let cwd = tempfile::tempdir().unwrap();
    let actual = serde_json::json!({"a": 1});
    let expect = serde_json::json!({"a": 1, "b": 2});
    assert_match(&expect, &actual, cwd.path(), "$");
}

// Shut up the "unused" warnings on the helpers that the live-execution
// runner will use once the bin → lib refactor lands.
#[test]
fn _unused_helper_check() {
    let d = tempfile::tempdir().unwrap();
    apply_setup(
        &d,
        &[SetupStep::Write {
            path: "hello.txt".into(),
            content: "x".into(),
        }],
    );
    assert!(d.path().join("hello.txt").exists());
    let _ = Arc::new(0usize);
}
