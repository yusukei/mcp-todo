import logging

import motor.motor_asyncio
from beanie import init_beanie

from .config import settings

logger = logging.getLogger(__name__)


async def connect() -> None:
    from ..models import AllowedEmail, McpApiKey, Project, Task, User

    client = motor.motor_asyncio.AsyncIOMotorClient(settings.MONGO_URI)
    await init_beanie(
        database=client[settings.MONGO_DBNAME],
        document_models=[User, AllowedEmail, Project, Task, McpApiKey],
    )
    logger.info("MongoDB connected: %s / %s", settings.MONGO_URI, settings.MONGO_DBNAME)
