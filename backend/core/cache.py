#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缓存服务模块

当前策略：
1. L1：优先 Redis（不落本地 sqlite）
2. L1 本地兜底：默认进程内内存缓存（非持久化）
3. L2-L4：MySQL 持久化缓存

说明：
- 默认禁止本地 sqlite（diskcache）以避免本地文件数据库问题。
- 仅当显式设置 CACHE_L1_BACKEND=diskcache 时才启用 diskcache。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, Optional

import redis
from sqlalchemy.orm import Session

from core.config import settings
from core.models import CacheEntry


class _MemoryL1Cache:
    """进程内 L1 缓存（非持久化），用于替代本地 sqlite。"""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    def set(self, key: str, value: Any, expire: int = 0) -> None:
        # 中文注释：expire<=0 表示不过期。
        expire_at = 0.0 if expire <= 0 else (time.time() + float(expire))
        self._store[key] = (expire_at, value)

    def get(self, key: str) -> Any:
        item = self._store.get(key)
        if not item:
            return None
        expire_at, value = item
        if expire_at > 0 and time.time() > expire_at:
            self._store.pop(key, None)
            return None
        return value

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def __getitem__(self, key: str) -> Any:
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def clear(self) -> None:
        self._store.clear()


class _NullL1Cache:
    """禁用本地 L1 的空实现。"""

    def set(self, key: str, value: Any, expire: int = 0) -> None:
        return None

    def get(self, key: str) -> Any:
        return None

    def __contains__(self, key: str) -> bool:
        return False

    def __getitem__(self, key: str) -> Any:
        raise KeyError(key)

    def clear(self) -> None:
        return None


class CacheService:
    def __init__(self, cache_dir: str = ".cache"):
        # 初始化 Redis（主 L1）
        self.redis_client = None
        try:
            self.redis_client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                decode_responses=True,
                socket_connect_timeout=1,
            )
            self.redis_client.ping()
            print(f"Redis connected: {settings.REDIS_HOST}:{settings.REDIS_PORT}")
        except Exception as e:
            print(f"Redis connection failed, fallback to local L1 only: {e}")
            self.redis_client = None

        # 中文注释：默认改为 memory，禁止默认使用本地 sqlite。
        self.l1_backend = os.getenv("CACHE_L1_BACKEND", "memory").strip().lower()
        self.l1_cache = self._init_local_l1(cache_dir)

        # 默认 TTL
        self.default_ttl = 3600
        self.ttl_config = {
            "L1": 3600,  # 1 Hour
            "L2": 86400,  # 24 Hours (Image/OCR Cache)
            "L3": 3600 * 12,  # 12 Hours (Context Compression)
            "L4": 3600 * 24 * 7,  # 7 Days (Final Generation)
        }

    def _init_local_l1(self, cache_dir: str):
        """按配置初始化本地 L1 缓存实现。"""
        if self.l1_backend == "none":
            print("Local L1 cache disabled (CACHE_L1_BACKEND=none).")
            return _NullL1Cache()

        if self.l1_backend == "diskcache":
            # 中文注释：仅显式配置时才启用 diskcache（本地 sqlite）。
            try:
                from diskcache import Cache as DiskCache  # 延迟导入，避免默认依赖 sqlite

                disk_dir = os.getenv("CACHE_L1_DIR", cache_dir)
                print(f"Local L1 cache enabled with diskcache: {disk_dir}")
                return DiskCache(disk_dir)
            except Exception as e:
                print(f"diskcache init failed, fallback to memory L1: {e}")
                return _MemoryL1Cache()

        # 默认 memory
        print("Local L1 cache backend: memory")
        return _MemoryL1Cache()

    def _calculate_hash(self, key_content: str) -> str:
        """计算键内容的 SHA256 哈希值。"""
        return hashlib.sha256(key_content.encode("utf-8")).hexdigest()

    def get(self, key_content: str, level: str, db: Session = None) -> Optional[Any]:
        """
        获取缓存值

        查找顺序：Redis (L1) -> 本地 L1(memory/disk/none) -> MySQL (L2-L4)
        """
        key_hash = self._calculate_hash(key_content)
        cache_key = f"{level}:{key_hash}"

        # 1. Redis
        if self.redis_client:
            val = self.redis_client.get(cache_key)
            if val is not None:
                try:
                    return json.loads(val)
                except Exception:
                    return val

        # 2. Local L1
        local_val = self.l1_cache.get(cache_key)
        if local_val is not None:
            return local_val

        # 3. MySQL
        if db:
            entry = (
                db.query(CacheEntry)
                .filter(CacheEntry.key_hash == key_hash, CacheEntry.cache_level == level)
                .first()
            )
            if entry:
                try:
                    val = json.loads(entry.value)
                except Exception:
                    val = entry.value
                self.set_l1(cache_key, val, level)
                return val

        return None

    def set_l1(self, cache_key: str, value: Any, level: str = "L1"):
        """设置 L1 缓存（Redis + 本地 L1）。"""
        ttl = self.ttl_config.get(level, self.default_ttl)

        if isinstance(value, (dict, list)):
            str_val = json.dumps(value, ensure_ascii=False)
        else:
            str_val = str(value)

        if self.redis_client:
            try:
                self.redis_client.set(cache_key, str_val, ex=ttl)
            except Exception:
                pass

        try:
            self.l1_cache.set(cache_key, value, expire=ttl)
        except Exception:
            # 中文注释：本地 L1 失败不影响主流程。
            pass

    def set(
        self,
        key_content: str,
        value: Any,
        level: str,
        db: Session = None,
        metadata: Dict = None,
    ):
        """设置缓存（写入 L1 + MySQL）。"""
        key_hash = self._calculate_hash(key_content)
        cache_key = f"{level}:{key_hash}"

        if isinstance(value, (dict, list)):
            str_val = json.dumps(value, ensure_ascii=False)
        else:
            str_val = str(value)

        self.set_l1(cache_key, value, level)

        if db:
            entry = (
                db.query(CacheEntry)
                .filter(CacheEntry.key_hash == key_hash, CacheEntry.cache_level == level)
                .first()
            )
            meta_str = json.dumps(metadata, ensure_ascii=False) if metadata else None
            if entry:
                entry.value = str_val
                entry.metadata_info = meta_str
            else:
                db.add(
                    CacheEntry(
                        key_hash=key_hash,
                        cache_level=level,
                        value=str_val,
                        metadata_info=meta_str,
                    )
                )
            try:
                db.commit()
            except Exception as e:
                print(f"Cache write failed: {e}")
                db.rollback()

    def clear_l1(self):
        """清空 L1 缓存（本地 + Redis）。"""
        try:
            self.l1_cache.clear()
        except Exception:
            pass
        if self.redis_client:
            # 中文注释：不执行 flushdb，避免误删非本系统 key。
            pass


# Global instance
cache_service = CacheService()

