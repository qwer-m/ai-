from __future__ import annotations

import json
from typing import Any

import redis

from core.cache_layer.redis_pool import redis_pool
from modules.memory_fabric.contracts.memory_context import MemoryContext


class RedisWorkingStore:
    def __init__(self) -> None:
        self._client = redis.Redis(connection_pool=redis_pool, decode_responses=True)

    def read(self, key: str, ctx: MemoryContext) -> Any:
        scoped_key = f"memory:l0:{ctx.scoped_key(key)}"
        raw = self._client.get(scoped_key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return raw

    def write(self, key: str, value: Any, ctx: MemoryContext, ttl: int | None = None) -> None:
        scoped_key = f"memory:l0:{ctx.scoped_key(key)}"
        payload = value
        if isinstance(value, (dict, list)):
            payload = json.dumps(value, ensure_ascii=False)
        if ttl and int(ttl) > 0:
            self._client.set(scoped_key, payload, ex=int(ttl))
            return
        self._client.set(scoped_key, payload)

