"""Tests for document export (Markdown + PDF)."""

import os
from pathlib import Path

import pytest
import pytest_asyncio


def _playwright_browsers_installed() -> bool:
    """Check if Playwright Chromium browser is installed."""
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or (
        Path.home() / ".cache" / "ms-playwright"
    )
    return any(Path(browsers_path).glob("chromium*/"))


requires_playwright = pytest.mark.skipif(
    not _playwright_browsers_installed(),
    reason="Playwright browsers not installed",
)

from app.models import Project, ProjectDocument, User
from app.models.document import DocumentCategory
from app.models.project import ProjectMember
from app.models.user import AuthType
from app.core.security import create_access_token, hash_password
from app.services.document_export import export_markdown, export_pdf


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def export_user():
    user = User(
        email="exporter@test.com",
        name="Exporter",
        auth_type=AuthType.admin,
        password_hash=hash_password("testpass8+"),
        is_admin=True,
        is_active=True,
    )
    await user.insert()
    return user


@pytest_asyncio.fixture
async def export_project(export_user):
    project = Project(
        name="Export Test Project",
        description="For testing exports",
        color="#6366f1",
        created_by=export_user,
        members=[ProjectMember(user_id=str(export_user.id))],
    )
    await project.insert()
    return project


@pytest_asyncio.fixture
async def sample_docs(export_project, export_user):
    docs = []
    d1 = ProjectDocument(
        project_id=str(export_project.id),
        title="設計ドキュメント",
        content="## 概要\n\nこれはテスト用の**設計ドキュメント**です。\n\n### 表\n\n| 列1 | 列2 |\n|-----|-----|\n| A   | B   |\n",
        tags=["design", "test"],
        category=DocumentCategory.design,
        created_by=str(export_user.id),
    )
    await d1.insert()
    docs.append(d1)

    d2 = ProjectDocument(
        project_id=str(export_project.id),
        title="API仕様書",
        content="## エンドポイント\n\n```python\n@app.get('/api/v1/items')\ndef list_items():\n    return []\n```\n\n### Mermaidダイアグラム\n\n```mermaid\nsequenceDiagram\n    Client->>Server: GET /api/v1/items\n    Server-->>Client: 200 OK\n```\n",
        tags=["api"],
        category=DocumentCategory.api,
        created_by=str(export_user.id),
    )
    await d2.insert()
    docs.append(d2)
    return docs


# ---------------------------------------------------------------------------
# Service-level tests
# ---------------------------------------------------------------------------

class TestExportMarkdown:
    async def test_single_document(self, sample_docs):
        result = export_markdown([sample_docs[0]])
        assert "# 設計ドキュメント" in result
        assert "これはテスト用の" in result

    async def test_multiple_documents_have_separator(self, sample_docs):
        result = export_markdown(sample_docs)
        assert "---" in result
        assert "# 設計ドキュメント" in result
        assert "# API仕様書" in result


class TestExportPdf:
    @requires_playwright
    async def test_generates_valid_pdf(self, sample_docs):
        """PDF generation with Playwright — may take a few seconds."""
        pdf_bytes = await export_pdf(sample_docs)
        # PDF files start with %PDF
        assert pdf_bytes[:5] == b"%PDF-"
        # Should be non-trivially sized
        assert len(pdf_bytes) > 1000


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestExportEndpoint:
    async def test_export_markdown_via_api(self, client, export_project, export_user, sample_docs):
        token = create_access_token(str(export_user.id))
        resp = await client.post(
            f"/api/v1/projects/{export_project.id}/documents/export",
            json={
                "document_ids": [str(d.id) for d in sample_docs],
                "format": "markdown",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert "text/markdown" in resp.headers["content-type"]
        body = resp.text
        assert "# 設計ドキュメント" in body
        assert "# API仕様書" in body

    @requires_playwright
    async def test_export_pdf_via_api(self, client, export_project, export_user, sample_docs):
        token = create_access_token(str(export_user.id))
        resp = await client.post(
            f"/api/v1/projects/{export_project.id}/documents/export",
            json={
                "document_ids": [str(d.id) for d in sample_docs],
                "format": "pdf",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content[:5] == b"%PDF-"

    async def test_export_no_docs_returns_404(self, client, export_project, export_user):
        token = create_access_token(str(export_user.id))
        resp = await client.post(
            f"/api/v1/projects/{export_project.id}/documents/export",
            json={
                "document_ids": ["000000000000000000000000"],
                "format": "markdown",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    async def test_export_invalid_format_returns_422(self, client, export_project, export_user):
        token = create_access_token(str(export_user.id))
        resp = await client.post(
            f"/api/v1/projects/{export_project.id}/documents/export",
            json={
                "document_ids": ["000000000000000000000000"],
                "format": "docx",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422
