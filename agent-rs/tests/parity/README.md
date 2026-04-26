# Parity test harness

Regression protection: replay JSON fixtures of `(input, expected)` pairs
against the Rust handlers and assert structural equivalence with the
Python agent's responses.

## Layout

```
parity/
├── README.md
├── fixtures/
│   ├── read_file/
│   │   ├── basic.json
│   │   └── ...
│   └── write_file/
│       └── ...
└── runner.rs                    # cargo test --test parity_runner
```

## Fixture format

```jsonc
{
  "description": "human-readable summary",
  "handler":     "read_file",      // request_type the dispatcher routes on
  "setup":       [                 // optional FS prep before the call
    { "kind": "write", "path": "hello.txt", "content": "hello world\n" }
  ],
  "input":       { "path": "hello.txt" },   // payload (cwd auto-injected)
  "expect":      {
    // shape: keys/values to assert. Templates supported on values:
    //   "{{cwd}}"           - the test tempdir absolute path
    //   "{{path:foo.txt}}"  - resolves to <cwd>/foo.txt (canonicalised)
    //   "{{any:int}}"       - value must be present and an integer
    //   "{{any:string}}"    - value must be present and a string
    //   "{{any}}"           - value must be present (any type)
    "content":     "hello world\n",
    "is_binary":   false,
    "size":        12,
    "encoding":    "utf-8",
    "path":        "{{path:hello.txt}}",
    "total_lines": 1,
    "truncated":   false
  }
}
```

## Adding fixtures

Two paths:

1. **Hand-written** — copy an existing fixture and edit. Best for one-off
   regressions and cases the Python tests don't yet cover.
2. **Auto-dumped from Python tests** — run `agent/scripts/dump_fixtures.py`
   (NOT YET IMPLEMENTED, see TODO below) to monkeypatch pytest and
   record `(msg, handler return value)` pairs from `test_remote_handlers.py`.
   This is the path that catches Python-side behaviour drift.

## TODO

- [ ] `agent/scripts/dump_fixtures.py`: pytest plugin that intercepts
  `test_remote_handlers.py::TestHandle*` tests and writes
  `fixtures/{handler}/{test_name}.json`. Until this lands, fixtures are
  hand-written.
- [ ] Add fixtures for: `mkdir`, `delete`, `move`, `copy`, `glob`, `tree`,
  `edit_file`, `grep`. Currently covered: `read_file`, `write_file`, `stat`,
  `list_dir`.
- [ ] CI step `cargo test --test parity_runner` (already runs as part of
  default `cargo test`, but will be its own step in agent-rs/09 once the
  fixture set grows).
