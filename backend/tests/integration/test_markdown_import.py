"""Integration tests for the Markdown import endpoints (documents + knowledge)."""

import io

import pytest_asyncio

from app.core.security import create_access_token, hash_password
from app.models import Project, User
from app.models.user import AuthType
from app.models.project import MemberRole, ProjectMember


@pytest_asyncio.fixture
async def owner_user():
    user = User(
        email="md-owner@test.com",
        name="MD Owner",
        auth_type=AuthType.admin,
        password_hash=hash_password("ownerpass"),
        is_admin=False,
        is_active=True,
    )
    await user.insert()
    return user


@pytest_asyncio.fixture
def owner_headers(owner_user):
    return {"Authorization": f"Bearer {create_access_token(str(owner_user.id))}"}


@pytest_asyncio.fixture
async def import_project(owner_user):
    project = Project(
        name="Import Project",
        created_by=owner_user,
        members=[ProjectMember(user_id=str(owner_user.id), role=MemberRole.owner)],
    )
    await project.insert()
    return project


def _md_file(name: str, body: str) -> tuple[str, io.BytesIO, str]:
    return (name, io.BytesIO(body.encode("utf-8")), "text/markdown")


# ──────────────────────────────────────────────
# Document import
# ──────────────────────────────────────────────


class TestImportDocuments:
    async def test_import_single_plain_markdown(
        self, client, import_project, owner_headers
    ):
        files = {
            "files": _md_file("design.md", "# Hello\n\nbody"),
        }
        resp = await client.post(
            f"/api/v1/projects/{import_project.id}/documents/import",
            files=files,
            headers=owner_headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["imported"] == 1
        assert body["skipped"] == 0
        doc = body["created"][0]
        assert doc["title"] == "design"
        assert "body" in doc["content"]
        # Default category for documents
        assert doc["category"] == "spec"

    async def test_import_with_frontmatter_overrides(
        self, client, import_project, owner_headers
    ):
        text = (
            "---\n"
            "title: Auth Spec\n"
            "tags: [auth, security]\n"
            "category: design\n"
            "---\n\n"
            "## Login\n\nflow"
        )
        files = {"files": _md_file("ignored.md", text)}
        resp = await client.post(
            f"/api/v1/projects/{import_project.id}/documents/import",
            files=files,
            headers=owner_headers,
        )
        assert resp.status_code == 201
        doc = resp.json()["created"][0]
        assert doc["title"] == "Auth Spec"
        assert doc["category"] == "design"
        assert sorted(doc["tags"]) == ["auth", "security"]
        assert "## Login" in doc["content"]

    async def test_import_multiple_files(
        self, client, import_project, owner_headers
    ):
        # httpx multipart accepts a list of (field, file) tuples
        files = [
            ("files", _md_file("a.md", "alpha")),
            ("files", _md_file("b.md", "beta")),
            ("files", _md_file("c.md", "gamma")),
        ]
        resp = await client.post(
            f"/api/v1/projects/{import_project.id}/documents/import",
            files=files,
            headers=owner_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["imported"] == 3
        titles = sorted(d["title"] for d in body["created"])
        assert titles == ["a", "b", "c"]

    async def test_import_unknown_category_falls_back_to_spec(
        self, client, import_project, owner_headers
    ):
        text = "---\ntitle: X\ncategory: nonexistent\n---\nbody"
        resp = await client.post(
            f"/api/v1/projects/{import_project.id}/documents/import",
            files={"files": _md_file("x.md", text)},
            headers=owner_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["created"][0]["category"] == "spec"

    async def test_import_rejects_non_markdown_extension(
        self, client, import_project, owner_headers
    ):
        files = {"files": _md_file("bad.txt", "not markdown")}
        resp = await client.post(
            f"/api/v1/projects/{import_project.id}/documents/import",
            files=files,
            headers=owner_headers,
        )
        assert resp.status_code == 201  # endpoint returns partial success
        body = resp.json()
        assert body["imported"] == 0
        assert body["skipped"] == 1
        assert body["errors"][0]["filename"] == "bad.txt"

    async def test_import_partial_success_mixes_md_and_non_md(
        self, client, import_project, owner_headers
    ):
        files = [
            ("files", _md_file("good.md", "valid")),
            ("files", _md_file("ignore.txt", "skip me")),
        ]
        resp = await client.post(
            f"/api/v1/projects/{import_project.id}/documents/import",
            files=files,
            headers=owner_headers,
        )
        body = resp.json()
        assert body["imported"] == 1
        assert body["skipped"] == 1

    async def test_import_rejects_invalid_utf8(
        self, client, import_project, owner_headers
    ):
        files = {
            "files": (
                "broken.md",
                io.BytesIO(b"\xff\xfe not valid utf8"),
                "text/markdown",
            ),
        }
        resp = await client.post(
            f"/api/v1/projects/{import_project.id}/documents/import",
            files=files,
            headers=owner_headers,
        )
        body = resp.json()
        assert body["imported"] == 0
        assert body["skipped"] == 1
        assert "UTF-8" in body["errors"][0]["error"]

    async def test_locked_project_blocks_import(
        self, client, owner_user, owner_headers
    ):
        project = Project(
            name="Locked",
            is_locked=True,
            created_by=owner_user,
            members=[ProjectMember(user_id=str(owner_user.id), role=MemberRole.owner)],
        )
        await project.insert()

        resp = await client.post(
            f"/api/v1/projects/{project.id}/documents/import",
            files={"files": _md_file("x.md", "body")},
            headers=owner_headers,
        )
        assert resp.status_code == 423


# ──────────────────────────────────────────────
# Knowledge import
# ──────────────────────────────────────────────


class TestImportKnowledge:
    async def test_import_single_plain_markdown(self, client, owner_headers):
        files = {"files": _md_file("recipe.md", "# Tip\n\nuse foo")}
        resp = await client.post(
            "/api/v1/knowledge/import", files=files, headers=owner_headers
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["imported"] == 1
        entry = body["created"][0]
        assert entry["title"] == "recipe"
        assert entry["category"] == "reference"
        assert "use foo" in entry["content"]

    async def test_import_with_frontmatter(self, client, owner_headers):
        text = (
            "---\n"
            "title: SQL Injection\n"
            "tags: [security, sql]\n"
            "category: troubleshooting\n"
            "---\n\n"
            "always parameterize"
        )
        resp = await client.post(
            "/api/v1/knowledge/import",
            files={"files": _md_file("x.md", text)},
            headers=owner_headers,
        )
        assert resp.status_code == 201
        entry = resp.json()["created"][0]
        assert entry["title"] == "SQL Injection"
        assert entry["category"] == "troubleshooting"
        assert sorted(entry["tags"]) == ["security", "sql"]

    async def test_import_unknown_category_falls_back_to_reference(
        self, client, owner_headers
    ):
        text = "---\ntitle: X\ncategory: nonexistent\n---\nbody"
        resp = await client.post(
            "/api/v1/knowledge/import",
            files={"files": _md_file("x.md", text)},
            headers=owner_headers,
        )
        assert resp.json()["created"][0]["category"] == "reference"

    async def test_import_rejects_non_markdown(self, client, owner_headers):
        resp = await client.post(
            "/api/v1/knowledge/import",
            files={"files": _md_file("foo.txt", "x")},
            headers=owner_headers,
        )
        body = resp.json()
        assert body["imported"] == 0
        assert body["skipped"] == 1

    async def test_import_multiple(self, client, owner_headers):
        files = [
            ("files", _md_file("k1.md", "one")),
            ("files", _md_file("k2.md", "two")),
        ]
        resp = await client.post(
            "/api/v1/knowledge/import", files=files, headers=owner_headers
        )
        body = resp.json()
        assert body["imported"] == 2
