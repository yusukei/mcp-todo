import redis.asyncio as aioredis

from .config import settings

_client: aioredis.Redis | None = None


def init_redis() -> aioredis.Redis:
    """Create and store the Redis client. Call once during startup (lifespan)."""
    global _client
    _client = aioredis.from_url(settings.REDIS_URI, decode_responses=True)
    return _client


def get_redis() -> aioredis.Redis:
    """Return the pre-initialized Redis client.

    Raises RuntimeError if called before init_redis().
    """
    if _client is None:
        raise RuntimeError(
            "Redis client is not initialized. "
            "Ensure init_redis() is called during application startup."
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
