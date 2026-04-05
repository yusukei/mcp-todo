"""Backup/restore endpoint integration tests."""

import asyncio
import io
import os
import zipfile
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


def _create_test_zip(
    db_content: bytes = b"fake db dump",
    assets: dict[str, dict[str, bytes]] | None = None,
) -> bytes:
    """Create a test zip backup in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("db.agz", db_content)
        if assets:
            for dir_name, files in assets.items():
                for fname, content in files.items():
                    zf.writestr(f"{dir_name}/{fname}", content)
    return buf.getvalue()


class TestExportBackup:
    async def test_admin_can_export(self, client, admin_user, admin_headers, tmp_path):
        docsite_dir = tmp_path / "docsite_assets"
        docsite_dir.mkdir()
        (docsite_dir / "page.html").write_text("<h1>test</h1>")

        bookmark_dir = tmp_path / "bookmark_assets"
        bookmark_dir.mkdir()
        (bookmark_dir / "thumb.png").write_bytes(b"fake png data")

        async def fake_mongodump(args, timeout=300):
            for a in args:
                if a.startswith("--archive="):
                    fpath = a.split("=", 1)[1]
                    os.makedirs(os.path.dirname(fpath), exist_ok=True)
                    with open(fpath, "wb") as f:
                        f.write(b"fake mongodump data")
            return (0, "", "")

        with patch("app.api.v1.endpoints.backup._run_subprocess", new_callable=AsyncMock) as mock_run, \
             patch("app.api.v1.endpoints.backup.settings") as mock_settings, \
             patch("app.api.v1.endpoints.backup.os.unlink"):
            mock_run.side_effect = fake_mongodump
            mock_settings.MONGO_URI = "mongodb://test:27017"
            mock_settings.MONGO_DBNAME = "test_db"
            mock_settings.DOCSITE_ASSETS_DIR = str(docsite_dir)
            mock_settings.BOOKMARK_ASSETS_DIR = str(bookmark_dir)

            resp = await client.post(EXPORT_URL, headers=admin_headers)

        assert resp.status_code == 200
        assert "application/zip" in resp.headers["content-type"]
        assert "backup_" in resp.headers.get("content-disposition", "")

        # Verify zip contents
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        assert "db.agz" in names
        assert "docsite_assets/page.html" in names
        assert "bookmark_assets/thumb.png" in names

    async def test_export_empty_assets(self, client, admin_user, admin_headers, tmp_path):
        """Export works when asset directories are empty or missing."""
        async def fake_mongodump(args, timeout=300):
            for a in args:
                if a.startswith("--archive="):
                    fpath = a.split("=", 1)[1]
                    os.makedirs(os.path.dirname(fpath), exist_ok=True)
                    with open(fpath, "wb") as f:
                        f.write(b"fake mongodump data")
            return (0, "", "")

        with patch("app.api.v1.endpoints.backup._run_subprocess", new_callable=AsyncMock) as mock_run, \
             patch("app.api.v1.endpoints.backup.settings") as mock_settings, \
             patch("app.api.v1.endpoints.backup.os.unlink"):
            mock_run.side_effect = fake_mongodump
            mock_settings.MONGO_URI = "mongodb://test:27017"
            mock_settings.MONGO_DBNAME = "test_db"
            mock_settings.DOCSITE_ASSETS_DIR = str(tmp_path / "nonexistent1")
            mock_settings.BOOKMARK_ASSETS_DIR = str(tmp_path / "nonexistent2")

            resp = await client.post(EXPORT_URL, headers=admin_headers)

        assert resp.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        assert "db.agz" in names
        assert len(names) == 1  # Only db.agz, no asset files

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
    async def test_admin_can_import_zip(self, client, admin_user, admin_headers):
        zip_data = _create_test_zip(
            assets={"docsite_assets": {"test.html": b"<h1>test</h1>"}}
        )

        with patch("app.api.v1.endpoints.backup._run_subprocess", new_callable=AsyncMock) as mock_run, \
             patch("app.api.v1.endpoints.backup._restore_assets") as mock_assets, \
             patch("app.api.v1.endpoints.backup._rebuild_search_indexes", new_callable=AsyncMock) as mock_rebuild:
            mock_run.return_value = (0, "", "")
            mock_assets.return_value = {"docsite_assets": 1, "bookmark_assets": 0}
            mock_rebuild.return_value = {"tasks": 10, "knowledge": 5}

            resp = await client.post(
                IMPORT_URL,
                headers=admin_headers,
                files={"file": ("backup_test.zip", zip_data, "application/zip")},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["assets_restored"]["docsite_assets"] == 1
        assert body["indexes_rebuilt"]["tasks"] == 10
        mock_assets.assert_called_once()
        mock_rebuild.assert_awaited_once()

    async def test_admin_can_import_legacy_agz(self, client, admin_user, admin_headers):
        """Backward compatibility: .agz files still accepted."""
        with patch("app.api.v1.endpoints.backup._run_subprocess", new_callable=AsyncMock) as mock_run, \
             patch("app.api.v1.endpoints.backup._rebuild_search_indexes", new_callable=AsyncMock) as mock_rebuild:
            mock_run.return_value = (0, "", "")
            mock_rebuild.return_value = {"tasks": 5}

            resp = await client.post(
                IMPORT_URL,
                headers=admin_headers,
                files={"file": ("backup_test.agz", b"fake archive", "application/gzip")},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["assets_restored"] == {}
        mock_rebuild.assert_awaited_once()

    async def test_file_too_large_rejected(self, client, admin_user, admin_headers):
        """Files exceeding MAX_BACKUP_SIZE are rejected with 413."""
        zip_data = _create_test_zip()

        with patch("app.api.v1.endpoints.backup.MAX_BACKUP_SIZE", 10):
            resp = await client.post(
                IMPORT_URL,
                headers=admin_headers,
                files={"file": ("backup.zip", zip_data, "application/zip")},
            )

        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"].lower()

    async def test_non_admin_forbidden(self, client, regular_user, user_headers):
        resp = await client.post(
            IMPORT_URL,
            headers=user_headers,
            files={"file": ("backup.zip", b"data", "application/zip")},
        )
        assert resp.status_code == 403

    async def test_unauthenticated_rejected(self, client):
        resp = await client.post(
            IMPORT_URL,
            files={"file": ("backup.zip", b"data", "application/zip")},
        )
        assert resp.status_code == 401

    async def test_invalid_file_extension(self, client, admin_user, admin_headers):
        resp = await client.post(
            IMPORT_URL,
            headers=admin_headers,
            files={"file": ("backup.tar", b"data", "application/x-tar")},
        )
        assert resp.status_code == 400
        assert ".zip" in resp.json()["detail"]

    async def test_no_filename_rejected(self, client, admin_user, admin_headers):
        resp = await client.post(
            IMPORT_URL,
            headers=admin_headers,
            files={"file": ("", b"data", "application/gzip")},
        )
        assert resp.status_code in (400, 422)

    async def test_invalid_zip_rejected(self, client, admin_user, admin_headers):
        resp = await client.post(
            IMPORT_URL,
            headers=admin_headers,
            files={"file": ("backup.zip", b"not a zip file", "application/zip")},
        )
        assert resp.status_code == 400
        assert "Invalid zip" in resp.json()["detail"]

    async def test_zip_missing_db_dump(self, client, admin_user, admin_headers):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("other.txt", "nothing useful")
        zip_data = buf.getvalue()

        resp = await client.post(
            IMPORT_URL,
            headers=admin_headers,
            files={"file": ("backup.zip", zip_data, "application/zip")},
        )
        assert resp.status_code == 400
        assert "db.agz" in resp.json()["detail"]

    async def test_import_subprocess_failure(self, client, admin_user, admin_headers):
        zip_data = _create_test_zip()

        with patch("app.api.v1.endpoints.backup._run_subprocess", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (1, "", "mongorestore: error")
            resp = await client.post(
                IMPORT_URL,
                headers=admin_headers,
                files={"file": ("backup.zip", zip_data, "application/zip")},
            )

        assert resp.status_code == 500
        assert "mongorestore failed" in resp.json()["detail"]

    async def test_import_timeout(self, client, admin_user, admin_headers):
        zip_data = _create_test_zip()

        with patch("app.api.v1.endpoints.backup._run_subprocess", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = asyncio.TimeoutError()
            resp = await client.post(
                IMPORT_URL,
                headers=admin_headers,
                files={"file": ("backup.zip", zip_data, "application/zip")},
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
        proc.communicate.side_effect = [asyncio.TimeoutError(), (b"", b"")]

        with patch("app.api.v1.endpoints.backup.asyncio.create_subprocess_exec", return_value=proc), \
             patch("app.api.v1.endpoints.backup.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            with pytest.raises(asyncio.TimeoutError):
                await _run_subprocess(["slow", "cmd"], timeout=1)

            proc.kill.assert_called_once()


class TestRestoreAssets:
    """Tests for _restore_assets helper."""

    def test_restores_assets_from_backup(self, tmp_path):
        from app.api.v1.endpoints.backup import _restore_assets

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "docsite_assets").mkdir()
        (work_dir / "docsite_assets" / "page.html").write_text("<h1>test</h1>")
        (work_dir / "bookmark_assets").mkdir()
        (work_dir / "bookmark_assets" / "img.png").write_bytes(b"png")

        target_docsite = tmp_path / "target_docsite"
        target_bookmark = tmp_path / "target_bookmark"

        with patch("app.api.v1.endpoints.backup.settings") as mock_settings:
            mock_settings.DOCSITE_ASSETS_DIR = str(target_docsite)
            mock_settings.BOOKMARK_ASSETS_DIR = str(target_bookmark)

            result = _restore_assets(str(work_dir))

        assert result["docsite_assets"] == 1
        assert result["bookmark_assets"] == 1
        assert (target_docsite / "page.html").exists()
        assert (target_bookmark / "img.png").exists()

    def test_clears_existing_assets(self, tmp_path):
        from app.api.v1.endpoints.backup import _restore_assets

        target_docsite = tmp_path / "target_docsite"
        target_docsite.mkdir()
        (target_docsite / "old.html").write_text("old")

        work_dir = tmp_path / "work"
        work_dir.mkdir()

        target_bookmark = tmp_path / "target_bookmark"

        with patch("app.api.v1.endpoints.backup.settings") as mock_settings:
            mock_settings.DOCSITE_ASSETS_DIR = str(target_docsite)
            mock_settings.BOOKMARK_ASSETS_DIR = str(target_bookmark)

            result = _restore_assets(str(work_dir))

        assert result["docsite_assets"] == 0
        assert result["bookmark_assets"] == 0
        assert not (target_docsite / "old.html").exists()
        assert target_docsite.exists()


class TestRebuildSearchIndexes:
    """Tests for _rebuild_search_indexes."""

    async def test_returns_not_available_when_tantivy_missing(self):
        from app.api.v1.endpoints.backup import _rebuild_search_indexes

        with patch("app.services.search.TANTIVY_AVAILABLE", False):
            result = await _rebuild_search_indexes()

        assert result["status"] == "tantivy_not_available"
