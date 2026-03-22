"""Redis バックエンド EventStore。

FastMCP の stateful SSE セッションイベントを Redis に永続化する。
サーバ再起動後も Last-Event-ID によるリジューム可能。
複数 uvicorn ワーカー間でセッション共有可能。
"""

from __future__ import annotations

import json
import logging

import redis.asyncio as aioredis
from mcp.server.streamable_http import (
    EventCallback,
    EventId,
    EventMessage,
    EventStore,
    StreamId,
)
from mcp.types import JSONRPCMessage
from pydantic import TypeAdapter

from .config import settings

logger = logging.getLogger(__name__)

_TTL = 3600  # SSEセッションイベントの保持時間（秒）
_KEY_PREFIX = "todo:mcp:events:"
_MAX_EVENTS_PER_STREAM = 1000

# JSONRPCMessage（Union型）のPydantic TypeAdapter
_message_adapter: TypeAdapter[JSONRPCMessage] = TypeAdapter(JSONRPCMessage)


class RedisEventStore(EventStore):
    """Redis List を使った EventStore 実装。

    各 stream_id に対して Redis List にイベントをアペンドし、
    Last-Event-ID 以降のイベントをリプレイする。

    event_id のフォーマット: "{stream_id}:{seq}"
    クライアントの Last-Event-ID をそのまま replay_events_after に渡すことで
    stream_id と seq を復元できる。
    """

    def __init__(self) -> None:
        self._redis: aioredis.Redis = aioredis.from_url(
            settings.REDIS_MCP_URI, decode_responses=True
        )

    def _key(self, stream_id: StreamId) -> str:
        return f"{_KEY_PREFIX}{stream_id}"

    async def store_event(
        self,
        stream_id: StreamId,
        message: JSONRPCMessage,
    ) -> EventId:
        key = self._key(stream_id)
        seq = await self._redis.incr(f"{key}:seq")
        # JSONRPCMessage はPydantic Union型なのでTypeAdapterでシリアライズ
        payload = json.dumps({
            "seq": seq,
            "data": _message_adapter.dump_python(message, mode="json"),
        })
        pipe = self._redis.pipeline()
        pipe.rpush(key, payload)
        pipe.ltrim(key, -_MAX_EVENTS_PER_STREAM, -1)
        pipe.expire(key, _TTL)
        pipe.expire(f"{key}:seq", _TTL)
        await pipe.execute()
        # event_id は "{stream_id}:{seq}" 形式。Last-Event-ID として返却され
        # replay_events_after でそのまま受け取る。
        return f"{stream_id}:{seq}"

    async def replay_events_after(
        self,
        last_event_id: EventId,
        send_callback: EventCallback,
    ) -> StreamId | None:
        # last_event_id は store_event が返した "{stream_id}:{seq}" 形式
        try:
            stream_id, last_seq_str = str(last_event_id).rsplit(":", 1)
            last_seq = int(last_seq_str)
        except (ValueError, IndexError):
            logger.warning("Invalid last_event_id format: %s", last_event_id)
            return None

        key = self._key(stream_id)
        raw_events = await self._redis.lrange(key, 0, -1)

        for raw in raw_events:
            try:
                stored = json.loads(raw)
                seq = stored["seq"]
                if seq > last_seq:
                    message = _message_adapter.validate_python(stored["data"])
                    await send_callback(EventMessage(
                        message=message,
                        event_id=f"{stream_id}:{seq}",
                    ))
            except Exception as e:
                logger.warning("Failed to replay event: %s", e)

        return StreamId(stream_id)

    async def aclose(self) -> None:
        await self._redis.aclose()
