"""Unit tests for agent self-update logic."""
from __future__ import annotations

import hashlib
import http.server
import socket
import threading
from pathlib import Path

import pytest

import self_update
from self_update import UpdateError


# ── cleanup_old_files ────────────────────────────────────────


def test_cleanup_old_files_removes_artifacts(tmp_path: Path) -> None:
    exe = tmp_path / "mcp-terminal-agent.exe"
    exe.write_bytes(b"CURRENT")
    (tmp_path / "mcp-terminal-agent.exe.new").write_bytes(b"half")
    (tmp_path / "mcp-terminal-agent.exe.old.1700000000").write_bytes(b"prev1")
    (tmp_path / "mcp-terminal-agent.exe.old.1700000500").write_bytes(b"prev2")
    (tmp_path / "readme.txt").write_bytes(b"docs")

    removed = self_update.cleanup_old_files(exe)
    assert removed == 3
    assert exe.exists()
    assert (tmp_path / "readme.txt").exists()
    assert not list(tmp_path.glob("*.new"))
    assert not list(tmp_path.glob("*.old.*"))


def test_cleanup_old_files_is_noop_when_nothing_stale(tmp_path: Path) -> None:
    exe = tmp_path / "mcp-terminal-agent.exe"
    exe.write_bytes(b"CURRENT")
    assert self_update.cleanup_old_files(exe) == 0
    assert exe.exists()


def test_cleanup_old_files_handles_missing_parent(tmp_path: Path) -> None:
    phantom = tmp_path / "does_not_exist" / "agent.exe"
    assert self_update.cleanup_old_files(phantom) == 0


# ── HTTP fixture ─────────────────────────────────────────────


class _FixedHandler(http.server.BaseHTTPRequestHandler):
    payload: bytes = b""
    expected_token: str | None = None

    def do_GET(self):  # noqa: N802
        if type(self).expected_token is not None:
            auth = self.headers.get("Authorization")
            if auth != f"Bearer {type(self).expected_token}":
                self.send_response(401)
                self.end_headers()
                return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(type(self).payload)))
        self.end_headers()
        self.wfile.write(type(self).payload)

    def log_message(self, format, *args):  # noqa: A002
        pass


def _serve(payload: bytes, token: str | None = None):
    handler_cls = type("H", (_FixedHandler,), {"payload": payload, "expected_token": token})
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = http.server.HTTPServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{port}/agent.bin", server


# ── _download ────────────────────────────────────────────────


def test_download_verifies_sha256(tmp_path: Path) -> None:
    payload = b"hello update world" * 128
    expected = hashlib.sha256(payload).hexdigest()
    url, server = _serve(payload)
    try:
        dest = tmp_path / "out.bin"
        n = self_update._download(url, dest, expected, token=None)
        assert n == len(payload)
        assert dest.read_bytes() == payload
    finally:
        server.shutdown()
        server.server_close()


def test_download_rejects_sha256_mismatch(tmp_path: Path) -> None:
    url, server = _serve(b"something different")
    try:
        dest = tmp_path / "out.bin"
        with pytest.raises(UpdateError, match="sha256 mismatch"):
            self_update._download(url, dest, "00" * 32, token=None)
        assert not dest.exists()
    finally:
        server.shutdown()
        server.server_close()


def test_download_passes_bearer_token(tmp_path: Path) -> None:
    payload = b"auth me"
    expected = hashlib.sha256(payload).hexdigest()
    url, server = _serve(payload, token="ta_secret")
    try:
        with pytest.raises(UpdateError):
            self_update._download(url, tmp_path / "x", expected, token="ta_wrong")
        dest = tmp_path / "y"
        n = self_update._download(url, dest, expected, token="ta_secret")
        assert n == len(payload)
    finally:
        server.shutdown()
        server.server_close()


# ── apply_update ─────────────────────────────────────────────


def test_apply_update_rename_swap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old_content = b"OLD-AGENT-BINARY"
    new_content = b"NEW-AGENT-BINARY-v2"
    exe = tmp_path / "mcp-terminal-agent.exe"
    exe.write_bytes(old_content)

    sha = hashlib.sha256(new_content).hexdigest()
    url, server = _serve(new_content)

    spawned: dict = {}

    def _fake_spawn(cmd_exe, argv):
        spawned["exe"] = cmd_exe
        spawned["argv"] = list(argv)

        class _P:
            pid = 424242

        return _P()

    monkeypatch.setattr(self_update, "_spawn_detached", _fake_spawn)

    try:
        old_path = self_update.apply_update(
            download_url=url,
            sha256=sha,
            version="9.9.9",
            token=None,
            restart_argv=["--url", "wss://x", "--token", "ta_xyz"],
            exe_path=exe,
            sleep_after_download=0,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert exe.read_bytes() == new_content
    assert old_path.exists()
    assert old_path.read_bytes() == old_content
    assert old_path.name.startswith("mcp-terminal-agent.exe.old.")
    assert spawned["exe"] == exe
    assert spawned["argv"] == ["--url", "wss://x", "--token", "ta_xyz"]
    assert not (tmp_path / "mcp-terminal-agent.exe.new").exists()


def test_apply_update_hash_mismatch_keeps_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / "agent.exe"
    exe.write_bytes(b"ORIGINAL")
    url, server = _serve(b"tampered payload")
    monkeypatch.setattr(
        self_update,
        "_spawn_detached",
        lambda *_a, **_k: pytest.fail("should not spawn on hash mismatch"),
    )

    try:
        with pytest.raises(UpdateError, match="sha256"):
            self_update.apply_update(
                download_url=url,
                sha256="deadbeef" * 8,
                version="1.0",
                token=None,
                restart_argv=[],
                exe_path=exe,
                sleep_after_download=0,
            )
    finally:
        server.shutdown()
        server.server_close()

    assert exe.read_bytes() == b"ORIGINAL"
    assert not (tmp_path / "agent.exe.new").exists()
    assert not list(tmp_path.glob("agent.exe.old.*"))


# ── _spawn_detached: BREAKAWAY_FROM_JOB fallback ────────────


def test_spawn_breakaway_fallback_on_access_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: spawn must retry without CREATE_BREAKAWAY_FROM_JOB
    when the parent process's job object forbids breakaway.

    On non-Windows the platform check skips the breakaway path entirely,
    so we pretend to be win32 and intercept subprocess.Popen.
    """
    monkeypatch.setattr(self_update.sys, "platform", "win32")

    calls: list[int] = []

    class _FakePopen:
        def __init__(self, cmd, *, creationflags, **kwargs):
            calls.append(creationflags)
            if creationflags & self_update._CREATE_BREAKAWAY_FROM_JOB:
                raise OSError(5, "Access is denied")
            self.pid = 4242

    monkeypatch.setattr(self_update.subprocess, "Popen", _FakePopen)

    result = self_update._spawn_detached(Path("C:/fake/agent.exe"), [])
    assert result.pid == 4242
    assert len(calls) == 2
    # Attempt 1 had breakaway; attempt 2 (after fallback) did not.
    assert calls[0] & self_update._CREATE_BREAKAWAY_FROM_JOB
    assert not (calls[1] & self_update._CREATE_BREAKAWAY_FROM_JOB)
    # Both retained DETACHED and NEW_PROCESS_GROUP.
    for flags in calls:
        assert flags & self_update._DETACHED_PROCESS
        assert flags & self_update._CREATE_NEW_PROCESS_GROUP


def test_spawn_breakaway_used_when_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the first attempt succeeds, no fallback retry happens."""
    monkeypatch.setattr(self_update.sys, "platform", "win32")

    calls: list[int] = []

    class _FakePopen:
        def __init__(self, cmd, *, creationflags, **kwargs):
            calls.append(creationflags)
            self.pid = 7777

    monkeypatch.setattr(self_update.subprocess, "Popen", _FakePopen)

    result = self_update._spawn_detached(Path("C:/fake/agent.exe"), ["--x"])
    assert result.pid == 7777
    assert len(calls) == 1
    assert calls[0] & self_update._CREATE_BREAKAWAY_FROM_JOB


def test_spawn_breakaway_propagates_other_oserrors(monkeypatch: pytest.MonkeyPatch) -> None:
    """If even the fallback (no-breakaway) spawn fails, the error must surface."""
    monkeypatch.setattr(self_update.sys, "platform", "win32")

    class _FakePopen:
        def __init__(self, cmd, *, creationflags, **kwargs):
            raise OSError(13, "Permission denied")

    monkeypatch.setattr(self_update.subprocess, "Popen", _FakePopen)

    with pytest.raises(OSError, match="Permission denied"):
        self_update._spawn_detached(Path("C:/fake/agent.exe"), [])


# ── User-Agent regression (Fix #4: Cloudflare 403 workaround) ─


class _UACapturingHandler(http.server.BaseHTTPRequestHandler):
    payload: bytes = b""
    captured_ua: list = []  # type: ignore[type-arg]

    def do_GET(self):  # noqa: N802
        type(self).captured_ua.append(self.headers.get("User-Agent"))
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(type(self).payload)))
        self.end_headers()
        self.wfile.write(type(self).payload)

    def log_message(self, format, *args):  # noqa: A002
        pass


def test_download_sets_user_agent(tmp_path: Path) -> None:
    """Regression: Cloudflare/WAFs reject the default ``Python-urllib`` UA
    with 403. The agent must send an identifiable, non-default UA.
    """
    payload = b"agent binary stub"
    expected = hashlib.sha256(payload).hexdigest()

    captured: list = []
    handler_cls = type(
        "H",
        (_UACapturingHandler,),
        {"payload": payload, "captured_ua": captured},
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = http.server.HTTPServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        dest = tmp_path / "out.bin"
        n = self_update._download(
            f"http://127.0.0.1:{port}/agent.bin",
            dest,
            expected,
            token=None,
        )
        assert n == len(payload)
    finally:
        server.shutdown()
        server.server_close()

    assert len(captured) == 1
    ua = captured[0]
    assert ua is not None
    # Must NOT be the default Python urllib UA (Cloudflare blocks it).
    assert not ua.lower().startswith("python-urllib")
    # Must identify the agent and include the version.
    assert "mcp-terminal-agent" in ua
    assert self_update.__version__ in ua

