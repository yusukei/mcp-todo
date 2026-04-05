"""Backup/restore endpoint integration tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


EXPORT_URL = "/api/v1/backup/export"
IMPORT_URL = "/api/v1/backup/import"


def _make_process_mock(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    """Create a mock for asyncio.create_subprocess_exec that returns the given results."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


class TestExportBackup:
    async def test_admin_can_export(self, client, admin_user, admin_headers):
        proc = _make_process_mock(returncode=0, stdout=b"done")

        with patch("app.api.v1.endpoints.backup.asyncio.create_subprocess_exec", return_value=proc) as mock_exec, \
             patch("app.api.v1.endpoints.backup.os.makedirs"), \
             patch("app.api.v1.endpoints.backup.FileResponse") as mock_file_resp:
            # FileResponse needs to be a real response for the test client to handle,
            # so instead we patch _run_subprocess directly and use a temp file.
            pass

        # Better approach: patch _run_subprocess and create a real temp file
        with patch("app.api.v1.endpoints.backup._run_subprocess", new_callable=AsyncMock) as mock_run, \
             patch("app.api.v1.endpoints.backup.os.makedirs"), \
             patch("app.api.v1.endpoints.backup.os.unlink"):
            mock_run.return_value = (0, "done", "")

            # Create a real temp file so FileResponse can read it
            import tempfile
            import os
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".agz")
            tmp.write(b"fake backup data")
            tmp.close()

            with patch("app.api.v1.endpoints.backup.os.path.join", return_value=tmp.name):
                resp = await client.post(EXPORT_URL, headers=admin_headers)

            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/gzip"
            assert "backup_" in resp.headers.get("content-disposition", "")
            assert resp.content == b"fake backup data"

            # Cleanup
            try:
                os.unlink(tmp.name)
            except FileNotFoundError:
                pass

    async def test_non_admin_forbidden(self, client, regular_user, user_headers):
        resp = await client.post(EXPORT_URL, headers=user_headers)
        assert resp.status_code == 403

    async def test_unauthenticated_rejected(self, client):
        resp = await client.post(EXPORT_URL)
        assert resp.status_code == 401

    async def test_export_subprocess_failure(self, client, admin_user, admin_headers):
        with patch("app.api.v1.endpoints.backup._run_subprocess", new_callable=AsyncMock) as mock_run, \
             patch("app.api.v1.endpoints.backup.os.makedirs"):
            mock_run.return_value = (1, "", "mongodump: error connecting")
            resp = await client.post(EXPORT_URL, headers=admin_headers)

        assert resp.status_code == 500
        assert "mongodump failed" in resp.json()["detail"]

    async def test_export_timeout(self, client, admin_user, admin_headers):
        with patch("app.api.v1.endpoints.backup._run_subprocess", new_callable=AsyncMock) as mock_run, \
             patch("app.api.v1.endpoints.backup.os.makedirs"):
            mock_run.side_effect = asyncio.TimeoutError()
            resp = await client.post(EXPORT_URL, headers=admin_headers)

        assert resp.status_code == 500
        assert "timed out" in resp.json()["detail"]


class TestImportBackup:
    async def test_admin_can_import(self, client, admin_user, admin_headers):
        with patch("app.api.v1.endpoints.backup._run_subprocess", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "", "")
            resp = await client.post(
                IMPORT_URL,
                headers=admin_headers,
                files={"file": ("backup_test.agz", b"fake archive data", "application/gzip")},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "Restore completed" in body["message"]

    async def test_non_admin_forbidden(self, client, regular_user, user_headers):
        resp = await client.post(
            IMPORT_URL,
            headers=user_headers,
            files={"file": ("backup.agz", b"data", "application/gzip")},
        )
        assert resp.status_code == 403

    async def test_unauthenticated_rejected(self, client):
        resp = await client.post(
            IMPORT_URL,
            files={"file": ("backup.agz", b"data", "application/gzip")},
        )
        assert resp.status_code == 401

    async def test_invalid_file_extension(self, client, admin_user, admin_headers):
        resp = await client.post(
            IMPORT_URL,
            headers=admin_headers,
            files={"file": ("backup.zip", b"data", "application/zip")},
        )
        assert resp.status_code == 400
        assert ".agz" in resp.json()["detail"]

    async def test_no_filename_rejected(self, client, admin_user, admin_headers):
        resp = await client.post(
            IMPORT_URL,
            headers=admin_headers,
            files={"file": ("", b"data", "application/gzip")},
        )
        # Empty filename may be rejected by FastAPI validation (422) or our check (400)
        assert resp.status_code in (400, 422)

    async def test_import_subprocess_failure(self, client, admin_user, admin_headers):
        with patch("app.api.v1.endpoints.backup._run_subprocess", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (1, "", "mongorestore: error")
            resp = await client.post(
                IMPORT_URL,
                headers=admin_headers,
                files={"file": ("backup.agz", b"data", "application/gzip")},
            )

        assert resp.status_code == 500
        assert "mongorestore failed" in resp.json()["detail"]

    async def test_import_timeout(self, client, admin_user, admin_headers):
        with patch("app.api.v1.endpoints.backup._run_subprocess", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = asyncio.TimeoutError()
            resp = await client.post(
                IMPORT_URL,
                headers=admin_headers,
                files={"file": ("backup.agz", b"data", "application/gzip")},
            )

        assert resp.status_code == 500
        assert "timed out" in resp.json()["detail"]


class TestBuildArgs:
    """Unit tests for the helper functions that build subprocess arguments."""

    def test_mongodump_args(self):
        from app.api.v1.endpoints.backup import _build_mongodump_args
        args = _build_mongodump_args()
        assert args[0] == "mongodump"
        assert any("--uri=" in a for a in args)
        assert "--gzip" in args
        assert "--archive" in args

    def test_mongorestore_args(self):
        from app.api.v1.endpoints.backup import _build_mongorestore_args
        args = _build_mongorestore_args()
        assert args[0] == "mongorestore"
        assert any("--uri=" in a for a in args)
        assert "--gzip" in args
        assert "--drop" in args
        assert any("--nsFrom=" in a for a in args)
        assert any("--nsTo=" in a for a in args)


class TestRunSubprocess:
    """Unit tests for _run_subprocess."""

    async def test_successful_run(self):
        from app.api.v1.endpoints.backup import _run_subprocess

        proc = _make_process_mock(returncode=0, stdout=b"output", stderr=b"")

        with patch("app.api.v1.endpoints.backup.asyncio.create_subprocess_exec", return_value=proc):
            returncode, stdout, stderr = await _run_subprocess(["echo", "test"])

        assert returncode == 0
        assert stdout == "output"
        assert stderr == ""

    async def test_failed_run(self):
        from app.api.v1.endpoints.backup import _run_subprocess

        proc = _make_process_mock(returncode=1, stdout=b"", stderr=b"error msg")

        with patch("app.api.v1.endpoints.backup.asyncio.create_subprocess_exec", return_value=proc):
            returncode, stdout, stderr = await _run_subprocess(["bad", "cmd"])

        assert returncode == 1
        assert stderr == "error msg"

    async def test_timeout_kills_process(self):
        from app.api.v1.endpoints.backup import _run_subprocess

        proc = AsyncMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        proc.kill = MagicMock()
        # After kill, communicate returns empty
        proc.communicate.side_effect = [asyncio.TimeoutError(), (b"", b"")]

        with patch("app.api.v1.endpoints.backup.asyncio.create_subprocess_exec", return_value=proc), \
             patch("app.api.v1.endpoints.backup.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            with pytest.raises(asyncio.TimeoutError):
                await _run_subprocess(["slow", "cmd"], timeout=1)

            proc.kill.assert_called_once()
