from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy.exc import IntegrityError

from core.cache_layer.cache import CacheService, _MemoryL1Cache


def test_redis_read_failure_continues_to_local_l1() -> None:
    """真实 Redis 客户端连接失败时，既定下层缓存仍然可读。"""

    service = CacheService.__new__(CacheService)
    service.redis_client = redis.Redis(
        host="127.0.0.1",
        port=1,
        db=0,
        socket_connect_timeout=0.05,
        socket_timeout=0.05,
        decode_responses=True,
        retry=Retry(NoBackoff(), 0),
    )
    service.l1_cache = _MemoryL1Cache()
    service.default_ttl = 3600
    service.ttl_config = {"L4": 3600}
    service._redis_disabled_until = 0.0
    service.redis_failure_cooldown_seconds = 30.0
    key_content = "redis-read-failure-must-continue"
    cache_key = f"L4:{service._calculate_hash(key_content)}"
    expected = {"source": "local_l1", "valid": True}
    service.l1_cache.set(cache_key, expected, expire=3600)

    assert service.get(key_content, "L4") == expected
    assert service._redis_disabled_until > time.monotonic()
    assert service.get(key_content, "L4") == expected


def test_concurrent_mysql_cache_insert_is_idempotent() -> None:
    """并发进程抢先写入同一键时，当前写入应回读并更新胜出记录。"""

    service = CacheService.__new__(CacheService)
    service.redis_client = None
    service.l1_cache = _MemoryL1Cache()
    service.default_ttl = 3600
    service.ttl_config = {"L4": 3600}

    winner = SimpleNamespace(
        cache_level="L4",
        value="old-value",
        metadata_info=None,
    )
    first_query = MagicMock()
    first_query.filter.return_value.first.return_value = None
    winner_query = MagicMock()
    winner_query.filter.return_value.first.return_value = winner
    db = MagicMock()
    db.query.side_effect = [first_query, winner_query]
    db.commit.side_effect = [
        IntegrityError("INSERT cache_entries", {}, Exception("duplicate key")),
        None,
    ]

    service.set(
        "same-model-request",
        {"text": "new-value"},
        "L4",
        db,
        metadata={"model": "glm-5.1"},
    )

    assert db.rollback.call_count == 1
    assert db.commit.call_count == 2
    assert winner.value == '{"text": "new-value"}'
    assert winner.metadata_info == '{"model": "glm-5.1"}'
