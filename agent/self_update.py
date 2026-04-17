"""Self-update support for the MCP Terminal Agent.

Implements the rename-swap update strategy described in the design doc:

1. Server pushes ``update_available`` over the WebSocket after the agent
   reports its version in ``agent_info``.
2. ``apply_update`` downloads the new binary to ``<exe>.new``, verifies
   the SHA-256, then renames the current binary to ``<exe>.old.<ts>``,
   moves the new binary into place, spawns a detached child, and asks
   the caller to terminate.
3. On startup, ``cleanup_old_files`` removes leftover ``.old.*`` and
   ``.new`` artifacts from previous updates.

This module is intentionally dependency-free (stdlib only) so the
PyInstaller bundle stays small and the update path can run even when
the third-party packages are damaged.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

__version__ = "0.4.0"

logger = logging.getLogger("workspace-agent.update")

# Detached / new-process group flags for Windows. Defined as plain ints so
# this module imports cleanly on POSIX (where the constants don't exist).
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# Environment variable used to skip the cleanup pass when running unit
# tests against a non-frozen interpreter.
_SKIP_CLEANUP_ENV = "MCP_AGENT_SKIP_UPDATE_CLEANUP"


class UpdateError(RuntimeError):
    """Raised when an update step fails in a recoverable way."""


def _is_frozen() -> bool:
    """Return True when running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def current_executable_path() -> Path:
    """Resolve the on-disk path of the running agent binary.

    When running from source (``python main.py``) we point at ``main.py``
    so callers can still exercise the update flow in tests, even though
    the rename trick obviously won't apply to a script.
    """
    if _is_frozen():
        return Path(sys.executable).resolve()
    # Source mode (dev / tests): use this module's parent main.py
    main_path = Path(__file__).resolve().parent / "main.py"
    return main_path


def _iter_stale_artifacts(exe_path: Path) -> Iterable[Path]:
    """Yield ``.old.*`` and ``.new`` siblings left over from previous updates."""
    parent = exe_path.parent
    stem = exe_path.name
    if not parent.exists():
        return
    for child in parent.iterdir():
        name = child.name
        if name == f"{stem}.new":
            yield child
        elif name.startswith(f"{stem}.old"):
            yield child


def cleanup_old_files(exe_path: Path | None = None) -> int:
    """Delete leftover update artifacts. Returns count successfully removed.

    Errors are logged at warning level and otherwise ignored — a stuck
    ``.old`` file should never block the agent from starting.
    """
    if os.environ.get(_SKIP_CLEANUP_ENV):
        return 0
    target = exe_path or current_executable_path()
    removed = 0
    for stale in _iter_stale_artifacts(target):
        try:
            stale.unlink()
            removed += 1
            logger.info("removed stale update artifact: %s", stale)
        except OSError as e:
            # On Windows the previous-process .old.exe may still be locked
            # if the predecessor hasn't fully exited. That's fine — we'll
            # try again on the next startup.
            logger.debug("could not remove %s: %s", stale, e)
    return removed


def _download(url: str, dest: Path, expected_sha256: str, token: str | None,
              chunk_size: int = 1024 * 1024, timeout: float = 300.0) -> int:
    """Download ``url`` to ``dest`` while verifying SHA-256 incrementally.

    Returns the number of bytes written. Raises ``UpdateError`` if the
    hash does not match (in which case ``dest`` is removed before
    returning so we never leave a tampered file behind).
    """
    req = urllib.request.Request(url)
    # Cloudflare and similar WAFs return 403 for the default
    # ``Python-urllib/x.y`` UA. Identify ourselves explicitly.
    req.add_header("User-Agent", f"mcp-workspace-agent/{__version__}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    sha = hashlib.sha256()
    written = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    sha.update(chunk)
                    f.write(chunk)
                    written += len(chunk)
    except urllib.error.URLError as e:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise UpdateError(f"download failed: {e}") from e
    digest = sha.hexdigest()
    if digest.lower() != expected_sha256.lower():
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise UpdateError(
            f"sha256 mismatch: expected {expected_sha256}, got {digest}"
        )
    return written


def _spawn_detached(exe: Path, argv: list[str]) -> subprocess.Popen:
    """Spawn ``exe`` so the new process survives the parent's exit.

    On Windows we use the detached / new-process-group flags. On POSIX we
    rely on ``start_new_session=True`` which puts the child in its own
    session and detaches from the parent's controlling terminal.
    """
    cmd = [str(exe), *argv]
    if sys.platform == "win32":
        base_flags = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
        popen_kwargs = dict(
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Prefer breakaway so the child survives even if the parent
        # is inside a job object (service wrappers, task scheduler,
        # CI runners). If the parent's job forbids breakaway the
        # CreateProcess call fails with ERROR_ACCESS_DENIED — in
        # that case fall back to a detached spawn without the flag,
        # which is still enough for interactive starts.
        try:
            return subprocess.Popen(
                cmd,
                creationflags=base_flags | _CREATE_BREAKAWAY_FROM_JOB,
                **popen_kwargs,
            )
        except OSError as e:
            logger.warning(
                "spawn with CREATE_BREAKAWAY_FROM_JOB failed (%s); "
                "retrying without breakaway",
                e,
            )
            return subprocess.Popen(
                cmd,
                creationflags=base_flags,
                **popen_kwargs,
            )
    return subprocess.Popen(
        cmd,
        start_new_session=True,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def apply_update(
    *,
    download_url: str,
    sha256: str,
    version: str,
    token: str | None,
    restart_argv: list[str],
    exe_path: Path | None = None,
    sleep_after_download: float = 0.5,
) -> Path:
    """Download a new binary, swap it in, and spawn the replacement.

    Steps:
        1. Download to ``<exe>.new`` while computing SHA-256.
        2. Verify hash; bail out if mismatch.
        3. Rename current exe to ``<exe>.old.<timestamp>``.
        4. Rename downloaded binary into the original location.
        5. Spawn the new binary detached, with ``restart_argv``.

    Returns the path of the relocated old binary so the caller can log it.
    Raises ``UpdateError`` on any recoverable failure (download, hash,
    rename) — caller should keep running on the old version.
    """
    target = (exe_path or current_executable_path()).resolve()
    new_path = target.with_name(target.name + ".new")
    timestamp = int(time.time())
    old_path = target.with_name(f"{target.name}.old.{timestamp}")

    # If a stale ``.new`` exists from a half-finished previous update,
    # nuke it so we can write a fresh download.
    if new_path.exists():
        try:
            new_path.unlink()
        except OSError as e:
            raise UpdateError(f"cannot remove stale {new_path}: {e}") from e

    logger.info("downloading update v%s from %s", version, download_url)
    bytes_written = _download(download_url, new_path, sha256, token)
    logger.info("downloaded %d bytes; sha256 verified", bytes_written)

    # Give Windows Defender / other AV scanners a moment to inspect the
    # freshly written file before we try to execute it. Skipping this can
    # cause CreateProcess to fail with ERROR_VIRUS_INFECTED on aggressive
    # AV setups even when the file is benign.
    if sleep_after_download > 0:
        time.sleep(sleep_after_download)

    # Step 3 — move running exe out of the way. On Windows this works
    # even though the file is locked, because the open handle was
    # opened with FILE_SHARE_DELETE.
    try:
        os.replace(target, old_path)
    except OSError as e:
        # Best-effort: remove the staged .new since we're aborting
        try:
            new_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise UpdateError(f"failed to move current exe aside: {e}") from e

    # Step 4 — promote the new binary
    try:
        os.replace(new_path, target)
    except OSError as e:
        # Try to roll back the rename so the agent can keep running
        try:
            os.replace(old_path, target)
        except OSError as rollback_err:
            logger.error(
                "rollback failed after promotion error: %s (rollback: %s)",
                e, rollback_err,
            )
        raise UpdateError(f"failed to promote new exe: {e}") from e

    # Step 5 — spawn the new process. If this fails we keep the new
    # binary in place so the next normal restart picks it up.
    try:
        _spawn_detached(target, restart_argv)
    except OSError as e:
        raise UpdateError(f"failed to spawn replacement: {e}") from e

    logger.info("update applied; old binary preserved at %s", old_path)
    return old_path


__all__ = [
    "__version__",
    "UpdateError",
    "apply_update",
    "cleanup_old_files",
    "current_executable_path",
]
