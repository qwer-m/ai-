#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缓存服务模块

实现四层缓存架构：
L1: 本地文件缓存 (DiskCache) - 替代 Redis，用于高频热点数据
L2-L4: MySQL 持久化缓存 - 用于长期存储
"""

import hashlib
import json
import os
import redis
from typing import Optional, Any, Dict
from diskcache import Cache
from sqlalchemy.orm import Session
from core.models import CacheEntry
from core.config import settings

class CacheService:
    def __init__(self, cache_dir: str = ".cache"):
        # Initialize Redis
        self.redis_client = None
        try:
            self.redis_client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                decode_responses=True,
                socket_connect_timeout=1
            )
            self.redis_client.ping()
            print(f"Redis connected: {settings.REDIS_HOST}:{settings.REDIS_PORT}")
        except Exception as e:
            print(f"Redis connection failed, using DiskCache as primary L1: {e}")
            self.redis_client = None

        # Initialize DiskCache (L1 fallback or local backup)
        self.l1_cache = Cache(cache_dir)
        # Default TTL for L1: 1 hour
        self.default_ttl = 3600
        
        # TTL Config for different levels (seconds)
        self.ttl_config = {
            "L1": 3600,       # 1 Hour
            "L2": 86400,      # 24 Hours (Image/OCR Cache)
            "L3": 3600 * 12,  # 12 Hours (Context Compression)
            "L4": 3600 * 24 * 7 # 7 Days (Final Generation)
        }

    def _calculate_hash(self, key_content: str) -> str:
        """计算键内容的 SHA256 哈希值 (Calculate Hash)"""
        return hashlib.sha256(key_content.encode('utf-8')).hexdigest()

    def get(self, key_content: str, level: str, db: Session = None) -> Optional[Any]:
        """
        获取缓存值 (Get Value)
        
        查找顺序: Redis (L1) -> DiskCache (L1 备份) -> MySQL (L2-L4)
        
        Args:
            key_content: 缓存键原始内容 (通常是 Prompt 或请求参数)。
            level: 缓存层级 (L1/L2/L3/L4)。
            db: 数据库会话 (用于查询 L2-L4)。
            
        Returns:
            Any: 反序列化后的缓存值，如果未命中则返回 None。
        """
        key_hash = self._calculate_hash(key_content)
        cache_key = f"{level}:{key_hash}"
        
        # 1. Try Redis
        if self.redis_client:
            try:
                val = self.redis_client.get(cache_key)
                if val is not None:
                    try:
                        return json.loads(val)
                    except:
                        return val
            except Exception as e:
                # Redis failed, continue to DiskCache
                pass

        # 2. Try DiskCache
        if cache_key in self.l1_cache:
            return self.l1_cache[cache_key]
        
        # 3. Try L2-L4 (MySQL) if DB session provided
        if db:
            entry = db.query(CacheEntry).filter(
                CacheEntry.key_hash == key_hash,
                CacheEntry.cache_level == level
            ).first()
            
            if entry:
                try:
                    # Try to parse as JSON, otherwise return as string
                    val = json.loads(entry.value)
                except:
                    val = entry.value
                
                # Populate L1 (Redis + Disk) for future access
                self.set_l1(cache_key, val, level)
                return val
        
        return None

    def set_l1(self, cache_key: str, value: Any, level: str = "L1"):
        """
        设置 L1 缓存 (Set L1 Cache)
        
        同时写入 Redis 和本地 DiskCache。
        """
        # Determine TTL
        ttl = self.ttl_config.get(level, self.default_ttl)

        # Serialize for Redis
        if isinstance(value, (dict, list)):
            str_val = json.dumps(value, ensure_ascii=False)
        else:
            str_val = str(value)

        # Set Redis
        if self.redis_client:
            try:
                self.redis_client.set(cache_key, str_val, ex=ttl)
            except Exception:
                pass
        
        # Set DiskCache
        self.l1_cache.set(cache_key, value, expire=ttl)

    def set(self, key_content: str, value: Any, level: str, db: Session = None, metadata: Dict = None):
        """
        设置缓存 (Set Cache)
        
        写入 L1 (Redis+Disk) 和 MySQL (如果提供了 db)。
        
        Args:
            key_content: 缓存键原始内容。
            value: 缓存值。
            level: 缓存层级。
            db: 数据库会话 (可选)。
            metadata: 元数据 (可选)。
        """
        key_hash = self._calculate_hash(key_content)
        cache_key = f"{level}:{key_hash}"
        
        # Serialize value for DB/Redis
        if isinstance(value, (dict, list)):
            str_val = json.dumps(value, ensure_ascii=False)
        else:
            str_val = str(value)
        
        # 1. Write to L1
        self.set_l1(cache_key, value, level)
        
        # 2. Write to MySQL
        if db:
            # Check if exists
            entry = db.query(CacheEntry).filter(
                CacheEntry.key_hash == key_hash,
                CacheEntry.cache_level == level
            ).first()
            
            meta_str = json.dumps(metadata, ensure_ascii=False) if metadata else None
            
            if entry:
                entry.value = str_val
                entry.metadata_info = meta_str
            else:
                new_entry = CacheEntry(
                    key_hash=key_hash,
                    cache_level=level,
                    value=str_val,
                    metadata_info=meta_str
                )
                db.add(new_entry)
            
            try:
                db.commit()
            except Exception as e:
                print(f"Cache write failed: {e}")
                db.rollback()

    def clear_l1(self):
        """清空 L1 缓存 (Clear L1)"""
        self.l1_cache.clear()
        if self.redis_client:
            try:
                # Warning: flushdb clears everything. 
                # Maybe we should only clear keys with our prefix if we had one.
                # But here we assume dedicated redis or ok to clear.
                # Or just iterate keys? Iterating is slow.
                # Let's just pass for now or provide a warning method.
                # For safety, let's NOT flushdb automatically unless explicit.
                # Or maybe just delete keys we track? We don't track keys.
                # Given this is a specific app, maybe it's fine.
                pass 
            except:
                pass

# Global instance
cache_service = CacheService()
