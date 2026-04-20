import asyncio
import time
import redis
import structlog
from typing import Optional

logger = structlog.get_logger()


class LocalRateLimiter:
    """Simple token bucket rate limiter for single instance"""
    
    def __init__(self, calls_per_second: int):
        self.rate = calls_per_second
        self.tokens = calls_per_second
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_update = now

            if self.tokens >= 1:
                self.tokens -= 1
                return True
            
            # Wait for token
            wait_time = (1 - self.tokens) / self.rate
            await asyncio.sleep(wait_time)
            self.tokens = 0
            return True


class DistributedRateLimiter:
    """Redis-based distributed rate limiter for multi-pod deployment"""
    
    def __init__(self, redis_url: str, calls_per_second: int, key_prefix: str = "rl"):
        self.rate = calls_per_second
        self.prefix = key_prefix
        self.fallback = LocalRateLimiter(calls_per_second)
        
        try:
            self.r = redis.Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_timeout=1,
                socket_connect_timeout=1
            )
            self.r.ping()
            logger.info("rate_limiter_redis_connected")
        except redis.RedisError as e:
            logger.warning("rate_limiter_redis_failed", error=str(e), fallback="local")
            self.r = None

    async def acquire(self) -> bool:
        """Acquire a token, blocking if necessary"""
        if not self.r:
            return await self.fallback.acquire()
        
        try:
            return await self._redis_acquire()
        except redis.RedisError as e:
            logger.warning("rate_limiter_error", error=str(e))
            return await self.fallback.acquire()

    async def _redis_acquire(self) -> bool:
        """Redis sliding window rate limiting"""
        key = f"{self.prefix}:{int(time.time())}"
        
        pipe = self.r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 2)
        results = pipe.execute()
        
        count = results[0]
        
        if count <= self.rate:
            return True
        
        # Rate exceeded, wait
        wait_time = 1.0 / self.rate
        await asyncio.sleep(wait_time)
        return await self._redis_acquire()

    def get_current_rate(self) -> Optional[int]:
        """Get current request count in this second"""
        if not self.r:
            return None
        
        try:
            key = f"{self.prefix}:{int(time.time())}"
            count = self.r.get(key)
            return int(count) if count else 0
        except redis.RedisError:
            return None


class RateLimiter:
    """Backward compatible wrapper"""
    
    def __init__(self, calls: int, period: int):
        self.calls = calls
        self.period = period
        self._limiter = LocalRateLimiter(calls // period if period else calls)

    def wrap(self, func):
        """Decorator for sync functions - deprecated, use async version"""
        import functools
        from ratelimit import limits, sleep_and_retry
        
        @sleep_and_retry
        @limits(calls=self.calls, period=self.period)
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper