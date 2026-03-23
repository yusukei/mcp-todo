import logging
import re

import motor.motor_asyncio
from beanie import init_beanie

from .config import settings

logger = logging.getLogger(__name__)


def _mask_uri(uri: str) -> str:
    """Mask password in MongoDB URI for safe logging."""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", uri)

_client: motor.motor_asyncio.AsyncIOMotorClient | None = None


async def connect() -> None:
    global _client
    from ..models import AllowedEmail, McpApiKey, Project, Task, User

    _client = motor.motor_asyncio.AsyncIOMotorClient(settings.MONGO_URI)
    await init_beanie(
        database=_client[settings.MONGO_DBNAME],
        document_models=[User, AllowedEmail, Project, Task, McpApiKey],
    )
    logger.info("MongoDB connected: %s / %s", _mask_uri(settings.MONGO_URI), settings.MONGO_DBNAME)


async def close_db() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


def get_mongo_client() -> motor.motor_asyncio.AsyncIOMotorClient | None:
    return _client
