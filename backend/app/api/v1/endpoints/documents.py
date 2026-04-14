import asyncio

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ....core.deps import get_current_user
from ....core.validators import valid_object_id
from ....models import Project, User
from ....models.document import DocumentCategory, DocumentVersion, ProjectDocument
from ....services.document_export import export_markdown, export_pdf
from ....services.document_search import index_document, deindex_document
from ....services.markdown_import import parse_markdown_file
from ....services.serializers import (
    document_to_dict as _document_dict,
    document_version_summary as _version_summary,
    document_version_to_dict as _version_dict,
)

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])

_VALID_CATEGORIES = {e.value for e in DocumentCategory}


class CreateDocumentRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field("", max_length=100000)
    tags: list[str] = Field(default_factory=list)
    category: str = "spec"


class UpdateDocumentRequest(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    content: str | None = Field(None, max_length=100000)
    tags: list[str] | None = None
    category: str | None = None
    task_id: str | None = None
    change_summary: str | None = None


class ReorderDocumentsRequest(BaseModel):
    document_ids: list[str] = Field(..., min_length=1)


class ExportDocumentsRequest(BaseModel):
    document_ids: list[str] = Field(..., min_length=1)
    format: str = Field("markdown", pattern=r"^(markdown|pdf)$")


# ── Helpers ──────────────────────────────────────────────────


async def _check_project_access(project_id: str, user: User) -> Project:
    """Return project if user is admin or member; raise 403 otherwise."""
    from ....models.project import ProjectStatus as _ProjectStatus

    valid_object_id(project_id)
    project = await Project.get(project_id)
    if not project or project.status == _ProjectStatus.archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not user.is_admin and not project.has_member(str(user.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access")
    return project


def _check_not_locked(project: Project) -> None:
    """Raise 423 if project is locked."""
    if project.is_locked:
        raise HTTPException(status.HTTP_423_LOCKED, "Project is locked")


# ── Endpoints ────────────────────────────────────────────────


_IMPORT_MAX_FILES = 50
_IMPORT_MAX_BYTES = 100_000  # matches the document content max_length


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_documents(
    project_id: str,
    files: list[UploadFile] = File(..., description="Markdown files to import"),
    user: User = Depends(get_current_user),
):
    """Import one or more Markdown files as project documents.

    Each file becomes a single document. The body becomes the `content`,
    the file name (sans `.md`) is the default title, and an optional
    YAML frontmatter block can override `title`, `tags`, and `category`.
    Files with unrecognized extensions are skipped with an error entry
    in the response so a partial import is still useful.
    """
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)

    if len(files) > _IMPORT_MAX_FILES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Too many files (max {_IMPORT_MAX_FILES})",
        )

    created: list[dict] = []
    errors: list[dict] = []

    for upload in files:
        name = upload.filename or "untitled.md"
        lower = name.lower()
        if not (lower.endswith(".md") or lower.endswith(".markdown")):
            errors.append({"filename": name, "error": "Not a Markdown file"})
            continue

        raw_bytes = await upload.read()
        if len(raw_bytes) > _IMPORT_MAX_BYTES:
            errors.append({"filename": name, "error": "File too large"})
            continue
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            errors.append({"filename": name, "error": "Not valid UTF-8"})
            continue

        parsed = parse_markdown_file(name, text)

        category_value = parsed.category if parsed.category in _VALID_CATEGORIES else "spec"

        d = ProjectDocument(
            project_id=project_id,
            title=parsed.title[:255],
            content=parsed.content[:_IMPORT_MAX_BYTES],
            tags=parsed.tags,
            category=DocumentCategory(category_value),
            created_by=str(user.id),
        )
        await d.insert()
        await index_document(d)
        created.append(_document_dict(d))

    return {
        "created": created,
        "errors": errors,
        "imported": len(created),
        "skipped": len(errors),
    }


@router.post("/export")
async def export_documents(
    project_id: str,
    body: ExportDocumentsRequest,
    user: User = Depends(get_current_user),
):
    """Export selected documents as Markdown or PDF."""
    await _check_project_access(project_id, user)

    try:
        oids = [ObjectId(did) for did in body.document_ids]
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid document ID")

    fetched = await ProjectDocument.find(
        {"_id": {"$in": oids}, "project_id": project_id, "is_deleted": False},
    ).to_list()

    if not fetched:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No documents found")

    # Preserve the order from the request (reflects UI sort_order)
    doc_map = {str(d.id): d for d in fetched}
    docs = [doc_map[did] for did in body.document_ids if did in doc_map]

    if body.format == "markdown":
        md_text = export_markdown(docs)
        filename = f"documents_{project_id[:8]}.md"
        return Response(
            content=md_text.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # PDF
    pdf_bytes = await export_pdf(docs)
    filename = f"documents_{project_id[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/reorder")
async def reorder_documents(
    project_id: str,
    body: ReorderDocumentsRequest,
    user: User = Depends(get_current_user),
):
    """Reorder documents by assigning sequential sort_order values."""
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)

    try:
        oids = [ObjectId(did) for did in body.document_ids]
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid document ID")

    docs = await ProjectDocument.find(
        {"_id": {"$in": oids}, "project_id": project_id, "is_deleted": False},
    ).to_list()

    doc_map = {str(d.id): d for d in docs}
    updates = []
    for i, did in enumerate(body.document_ids):
        doc = doc_map.get(did)
        if doc and doc.sort_order != i:
            doc.sort_order = i
            updates.append(doc.save())
    if updates:
        await asyncio.gather(*updates)

    return {"reordered": len(updates)}


@router.get("/")
async def list_documents(
    project_id: str,
    category: str | None = Query(None),
    tag: str | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(50, ge=1),
    skip: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
):
    await _check_project_access(project_id, user)

    filters: dict = {"project_id": project_id, "is_deleted": False}
    if category:
        if category not in _VALID_CATEGORIES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid category: {category}")
        filters["category"] = category
    if tag:
        filters["tags"] = tag.lower()
    if search:
        import re
        pattern = re.escape(search.strip())
        filters["$or"] = [
            {"title": {"$regex": pattern, "$options": "i"}},
            {"content": {"$regex": pattern, "$options": "i"}},
            {"tags": {"$regex": pattern, "$options": "i"}},
        ]

    total = await ProjectDocument.find(filters).count()
    docs = await ProjectDocument.find(filters).skip(skip).limit(limit).sort("+sort_order", "-updated_at").to_list()
    return {"items": [_document_dict(d) for d in docs], "total": total, "limit": limit, "skip": skip}


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_document(
    project_id: str,
    body: CreateDocumentRequest,
    user: User = Depends(get_current_user),
):
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)

    if body.category not in _VALID_CATEGORIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid category: {body.category}")

    normalized_tags = [t.strip().lower() for t in body.tags if t.strip()]

    d = ProjectDocument(
        project_id=project_id,
        title=body.title.strip(),
        content=body.content,
        tags=normalized_tags,
        category=DocumentCategory(body.category),
        created_by=str(user.id),
    )
    await d.insert()
    await index_document(d)
    return _document_dict(d)


@router.get("/{document_id}")
async def get_document(
    project_id: str,
    document_id: str,
    user: User = Depends(get_current_user),
):
    await _check_project_access(project_id, user)
    d = await ProjectDocument.get(document_id)
    if not d or d.is_deleted or d.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return _document_dict(d)


@router.patch("/{document_id}")
async def update_document(
    project_id: str,
    document_id: str,
    body: UpdateDocumentRequest,
    user: User = Depends(get_current_user),
):
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)

    d = await ProjectDocument.get(document_id)
    if not d or d.is_deleted or d.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    if body.category is not None and body.category not in _VALID_CATEGORIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid category: {body.category}")

    # Snapshot current state as a version
    version = DocumentVersion(
        document_id=str(d.id),
        version=d.version,
        title=d.title,
        content=d.content,
        tags=list(d.tags),
        category=d.category,
        changed_by=str(user.id),
        task_id=body.task_id,
        change_summary=body.change_summary,
    )
    await version.insert()

    # Apply updates
    if body.title is not None:
        d.title = body.title.strip()
    if body.content is not None:
        d.content = body.content
    if body.tags is not None:
        d.tags = [t.strip().lower() for t in body.tags if t.strip()]
    if body.category is not None:
        d.category = DocumentCategory(body.category)
    d.version += 1

    await d.save_updated()
    await index_document(d)
    return _document_dict(d)


@router.get("/{document_id}/versions")
async def list_document_versions(
    project_id: str,
    document_id: str,
    limit: int = Query(20, ge=1),
    skip: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
):
    await _check_project_access(project_id, user)
    d = await ProjectDocument.get(document_id)
    if not d or d.is_deleted or d.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    total = await DocumentVersion.find(
        DocumentVersion.document_id == str(d.id),
    ).count()
    versions = await DocumentVersion.find(
        DocumentVersion.document_id == str(d.id),
    ).sort("-version").skip(skip).limit(limit).to_list()

    return {
        "document_id": str(d.id),
        "current_version": d.version,
        "items": [_version_summary(v) for v in versions],
        "total": total,
    }


@router.get("/{document_id}/versions/{version_num}")
async def get_document_version(
    project_id: str,
    document_id: str,
    version_num: int,
    user: User = Depends(get_current_user),
):
    await _check_project_access(project_id, user)
    d = await ProjectDocument.get(document_id)
    if not d or d.is_deleted or d.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    v = await DocumentVersion.find_one(
        DocumentVersion.document_id == str(d.id),
        DocumentVersion.version == version_num,
    )
    if not v:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Version {version_num} not found")

    return _version_dict(v)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    project_id: str,
    document_id: str,
    user: User = Depends(get_current_user),
):
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)

    d = await ProjectDocument.get(document_id)
    if not d or d.is_deleted or d.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    d.is_deleted = True
    await d.save_updated()
    await deindex_document(document_id)
