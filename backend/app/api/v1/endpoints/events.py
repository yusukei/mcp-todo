import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from ....core.redis import get_redis
from ....core.security import decode_access_token
from ....models import Project, User
from ....models.project import ProjectStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
async def sse_stream(token: str = Query(..., description="JWT access token")) -> StreamingResponse:
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = await User.get(payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    # Build set of project IDs user has access to
    if user.is_admin:
        user_project_ids: set[str] | None = None  # admin = all projects
    else:
        projects = await Project.find(
            Project.status == ProjectStatus.active,
            Project.members.user_id == str(user.id),
        ).to_list()
        user_project_ids = {str(p.id) for p in projects}

    async def generator():
        redis = get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe("todo:events")
        try:
            yield "data: {\"type\": \"connected\"}\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                        timeout=30,
                    )
                except asyncio.TimeoutError:
                    message = None

                if message and message["type"] == "message":
                    # Project filtering
                    if user_project_ids is not None:
                        try:
                            event_data = json.loads(message["data"])
                            pid = event_data.get("project_id")
                            if pid and pid not in user_project_ids:
                                continue
                        except (json.JSONDecodeError, TypeError):
                            pass
                    yield f"data: {message['data']}\n\n"
                else:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe("todo:events")
            await pubsub.aclose()

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
