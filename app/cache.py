import time
import json
import redis
import structlog
from typing import Any, Optional

logger = structlog.get_logger()


class TTLCache:
    """In-memory fallback cache"""
    def __init__(self, ttl: int):
        self.ttl = ttl
        self.store = {}

    def set(self, key: str, value: Any):
        self.store[key] = (value, time.time())

    def get(self, key: str) -> Optional[Any]:
        v = self.store.get(key)
        if not v:
            return None
        val, ts = v
        if time.time() - ts > self.ttl:
            del self.store[key]
            return None
        return val

    def delete(self, key: str):
        self.store.pop(key, None)

    def clear(self):
        self.store.clear()


class RedisCache:
    """Redis-based distributed cache for multi-pod deployment"""
    
    def __init__(self, url: str, ttl: int, prefix: str = "mcp"):
        self.ttl = ttl
        self.prefix = prefix
        self.fallback = TTLCache(ttl)
        self._connected = False
        
        try:
            self.r = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5,
                retry_on_timeout=True
            )
            self.r.ping()
            self._connected = True
            logger.info("redis_connected", url=url)
        except redis.RedisError as e:
            logger.warning("redis_connection_failed", error=str(e), fallback="memory")
            self.r = None

    def _key(self, key: str) -> str:
        return f"{self.prefix}:{key}"

    def set(self, key: str, value: Any) -> bool:
        try:
            if self.r:
                self.r.setex(self._key(key), self.ttl, json.dumps(value))
                return True
        except redis.RedisError as e:
            logger.warning("redis_set_error", key=key, error=str(e))
        
        # Fallback to memory
        self.fallback.set(key, value)
        return False

    def get(self, key: str) -> Optional[Any]:
        try:
            if self.r:
                val = self.r.get(self._key(key))
                if val:
                    return json.loads(val)
        except redis.RedisError as e:
            logger.warning("redis_get_error", key=key, error=str(e))
        
        # Fallback to memory
        return self.fallback.get(key)

    def delete(self, key: str) -> bool:
        try:
            if self.r:
                self.r.delete(self._key(key))
                return True
        except redis.RedisError:
            pass
        
        self.fallback.delete(key)
        return False

    def get_all(self, pattern: str = "*") -> dict:
        """Get all keys matching pattern"""
        result = {}
        try:
            if self.r:
                keys = self.r.keys(self._key(pattern))
                for key in keys:
                    val = self.r.get(key)
                    if val:
                        # Remove prefix from key name
                        clean_key = key.replace(f"{self.prefix}:", "", 1)
                        result[clean_key] = json.loads(val)
        except redis.RedisError as e:
            logger.warning("redis_get_all_error", error=str(e))
            # Return fallback store
            return {k: v for k, (v, _) in self.fallback.store.items()}
        
        return result

    def is_connected(self) -> bool:
        if not self.r:
            return False
        try:
            self.r.ping()
            return True
        except redis.RedisError:
            return False

    def health_check(self) -> dict:
        return {
            "redis_connected": self.is_connected(),
            "fallback_items": len(self.fallback.store)
        }