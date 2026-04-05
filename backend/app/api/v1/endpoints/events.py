import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ....core.deps import get_current_user
from ....core.redis import get_redis
from ....models import Project, User
from ....models.project import ProjectStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/events", tags=["events"])


def _should_skip_event(user_project_ids: set[str] | None, message_data: str) -> bool:
    """Determine whether an SSE event should be skipped for the given user.

    Args:
        user_project_ids: Set of project IDs the user has access to,
                          or None if the user is an admin (sees everything).
        message_data: Raw JSON string of the event message.

    Returns:
        True if the event should be skipped (not sent to the user).
    """
    if user_project_ids is not None:
        try:
            event_data = json.loads(message_data)
            pid = event_data.get("project_id")
            if pid and pid not in user_project_ids:
                return True
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
    return False


_SSE_TICKET_TTL = 30  # seconds
_SSE_TICKET_PREFIX = "sse_ticket:"


class TicketResponse(BaseModel):
    ticket: str


@router.post("/ticket", response_model=TicketResponse)
async def create_sse_ticket(user: User = Depends(get_current_user)) -> TicketResponse:
    """Issue a short-lived, single-use ticket for SSE connection.

    The ticket is stored in Redis with a 30-second TTL and maps to
    the authenticated user's ID.  This avoids exposing the JWT in
    the EventSource URL query string.
    """
    ticket = uuid.uuid4().hex
    redis = get_redis()
    await redis.set(f"{_SSE_TICKET_PREFIX}{ticket}", str(user.id), ex=_SSE_TICKET_TTL)
    return TicketResponse(ticket=ticket)


@router.get("")
async def sse_stream(ticket: str = Query(..., description="One-time SSE ticket")) -> StreamingResponse:
    redis = get_redis()

    # Validate and consume ticket (one-time use)
    ticket_key = f"{_SSE_TICKET_PREFIX}{ticket}"
    user_id = await redis.get(ticket_key)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired ticket")
    await redis.delete(ticket_key)

    user = await User.get(user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    # Build set of project IDs user has access to
    if user.is_admin:
        user_project_ids: set[str] | None = None  # admin = all projects
    else:
        # Use motor collection with projection to fetch only _id fields,
        # avoiding full document deserialization
        col = Project.get_motor_collection()
        cursor = col.find(
            {"status": ProjectStatus.active, "members.user_id": str(user.id)},
            {"_id": 1},
        )
        user_project_ids = {str(doc["_id"]) async for doc in cursor}

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
                    if _should_skip_event(user_project_ids, message["data"]):
                        continue
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
