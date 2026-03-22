import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from ....core.redis import get_redis
from ....core.security import decode_token
from ....models import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
async def sse_stream(token: str = Query(..., description="JWT access token")) -> StreamingResponse:
    # EventSource APIはカスタムヘッダー不可のためURLトークン方式で認証
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = await User.get(payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    async def generator():
        redis = get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe("todo:events")
        try:
            yield "data: {\"type\": \"connected\"}\n\n"
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30)
                if message and message["type"] == "message":
                    yield f"data: {message['data']}\n\n"
                else:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.1)
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
