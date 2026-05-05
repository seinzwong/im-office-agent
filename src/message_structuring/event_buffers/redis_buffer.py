from __future__ import annotations

import json

try:
    import redis
except Exception:  # pragma: no cover
    redis = None


class RedisEventBuffer:
    def __init__(
        self,
        redis_url: str,
        key_prefix: str = "msl",
        ttl_seconds: int = 300,
        max_messages: int = 1000,
    ) -> None:
        if redis is None:
            raise RuntimeError("redis package is not installed")
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)
        self.prefix = key_prefix
        self.ttl_seconds = ttl_seconds
        self.max_messages = max_messages

    def _key(self, chat_id: str) -> str:
        return f"{self.prefix}:chat:{chat_id}:event_buffer"

    def append(self, chat_id: str, event: dict) -> None:
        key = self._key(chat_id)
        payload = json.dumps(event, ensure_ascii=False)
        with self.client.pipeline() as pipe:
            pipe.rpush(key, payload)
            pipe.ltrim(key, -self.max_messages, -1)
            pipe.expire(key, self.ttl_seconds)
            pipe.execute()

    def get(self, chat_id: str) -> list[dict]:
        key = self._key(chat_id)
        rows = self.client.lrange(key, 0, -1)
        return [json.loads(row) for row in rows]

    def clear(self, chat_id: str) -> None:
        self.client.delete(self._key(chat_id))
