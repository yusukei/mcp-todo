from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from ....core.deps import get_current_user
from ....models import User
from ....models.knowledge import Knowledge, KnowledgeCategory
from ....services.knowledge_search import index_knowledge, deindex_knowledge
from ....services.markdown_import import parse_markdown_file
from ....services.serializers import knowledge_to_dict as _knowledge_dict

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

_VALID_CATEGORIES = {e.value for e in KnowledgeCategory}


class CreateKnowledgeRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field("", max_length=50000)
    tags: list[str] = Field(default_factory=list)
    category: str = "reference"
    source: str | None = None


class UpdateKnowledgeRequest(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    content: str | None = Field(None, max_length=50000)
    tags: list[str] | None = None
    category: str | None = None
    source: str | None = None


@router.get("/")
async def list_knowledge(
    category: str | None = Query(None),
    tag: str | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
):
    filters: dict = {"is_deleted": False}
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

    total = await Knowledge.find(filters).count()
    entries = await Knowledge.find(filters).skip(skip).limit(limit).sort("-updated_at").to_list()
    return {"items": [_knowledge_dict(e) for e in entries], "total": total, "limit": limit, "skip": skip}


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_knowledge(
    body: CreateKnowledgeRequest,
    user: User = Depends(get_current_user),
):
    if body.category not in _VALID_CATEGORIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid category: {body.category}")

    normalized_tags = [t.strip().lower() for t in body.tags if t.strip()]

    k = Knowledge(
        title=body.title.strip(),
        content=body.content,
        tags=normalized_tags,
        category=KnowledgeCategory(body.category),
        source=body.source,
        created_by=str(user.id),
    )
    await k.insert()
    await index_knowledge(k)
    return _knowledge_dict(k)


_IMPORT_MAX_FILES = 50
_IMPORT_MAX_BYTES = 50_000  # matches the knowledge content max_length


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_knowledge(
    files: list[UploadFile] = File(..., description="Markdown files to import"),
    user: User = Depends(get_current_user),
):
    """Import one or more Markdown files as knowledge entries.

    Same parsing rules as the project document import: optional YAML
    frontmatter (`title`, `tags`, `category`), file name as fallback
    title, body as content. Files with unsupported extensions or invalid
    UTF-8 are skipped with an error entry so partial imports work.
    """
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

        category_value = (
            parsed.category if parsed.category in _VALID_CATEGORIES else "reference"
        )

        k = Knowledge(
            title=parsed.title[:255],
            content=parsed.content[:_IMPORT_MAX_BYTES],
            tags=parsed.tags,
            category=KnowledgeCategory(category_value),
            created_by=str(user.id),
        )
        await k.insert()
        await index_knowledge(k)
        created.append(_knowledge_dict(k))

    return {
        "created": created,
        "errors": errors,
        "imported": len(created),
        "skipped": len(errors),
    }


@router.get("/{knowledge_id}")
async def get_knowledge(
    knowledge_id: str,
    user: User = Depends(get_current_user),
):
    k = await Knowledge.get(knowledge_id)
    if not k or k.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knowledge entry not found")
    return _knowledge_dict(k)


@router.patch("/{knowledge_id}")
async def update_knowledge(
    knowledge_id: str,
    body: UpdateKnowledgeRequest,
    user: User = Depends(get_current_user),
):
    k = await Knowledge.get(knowledge_id)
    if not k or k.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knowledge entry not found")

    if body.category is not None and body.category not in _VALID_CATEGORIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid category: {body.category}")

    if body.title is not None:
        k.title = body.title.strip()
    if body.content is not None:
        k.content = body.content
    if body.tags is not None:
        k.tags = [t.strip().lower() for t in body.tags if t.strip()]
    if body.category is not None:
        k.category = KnowledgeCategory(body.category)
    if body.source is not None:
        k.source = body.source if body.source else None

    await k.save_updated()
    await index_knowledge(k)
    return _knowledge_dict(k)


@router.delete("/{knowledge_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge(
    knowledge_id: str,
    user: User = Depends(get_current_user),
):
    k = await Knowledge.get(knowledge_id)
    if not k or k.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knowledge entry not found")

    k.is_deleted = True
    await k.save_updated()
    await deindex_knowledge(knowledge_id)
