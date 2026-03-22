import json
import logging

from ..core.redis import get_redis

logger = logging.getLogger(__name__)

_CHANNEL = "todo:events"


async def publish_event(project_id: str, event_type: str, data: dict) -> None:
    try:
        redis = get_redis()
        payload = json.dumps({"type": event_type, "project_id": project_id, "data": data})
        await redis.publish(_CHANNEL, payload)
    except Exception as e:
        logger.warning("Failed to publish event %s: %s", event_type, e)
