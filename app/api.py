from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Counter, Gauge
import structlog

from .config_loader import load_config
from .cache import RedisCache

logger = structlog.get_logger()

# Metrics
request_count = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
cache_items = Gauge('cache_items_total', 'Total items in cache')

# Global cache instance
cache = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup/shutdown"""
    global cache
    
    cfg = load_config()
    cache = RedisCache(
        url=cfg["redis"]["url"],
        ttl=cfg["cache"]["ttl_sec"]
    )
    logger.info("api_started", redis_connected=cache.is_connected())
    
    yield
    
    logger.info("api_shutdown")


app = FastAPI(
    title="Zabbix MCP API",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/health")
async def health():
    """Kubernetes liveness probe"""
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    """Kubernetes readiness probe"""
    if cache and cache.is_connected():
        return {"status": "ready", "redis": "connected"}
    return JSONResponse(
        {"status": "degraded", "redis": "disconnected"},
        status_code=503
    )


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    if cache:
        cache_items.set(len(cache.get_all()))
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


@app.get("/analysis")
async def analysis():
    """Get all analysis results"""
    request_count.labels(method="GET", endpoint="/analysis", status="200").inc()
    
    if not cache:
        return JSONResponse({"error": "Cache not initialized"}, status_code=503)
    
    data = cache.get_all("analysis:*")
    return {
        "count": len(data),
        "data": data
    }


@app.get("/analysis/{group_name}")
async def analysis_by_group(group_name: str):
    """Get analysis for specific group"""
    if not cache:
        return JSONResponse({"error": "Cache not initialized"}, status_code=503)
    
    data = cache.get(f"analysis:{group_name}")
    if not data:
        return JSONResponse({"error": "Group not found"}, status_code=404)
    
    request_count.labels(method="GET", endpoint="/analysis/{group}", status="200").inc()
    return data


@app.get("/status")
async def status():
    """System status overview"""
    if not cache:
        return {"status": "initializing"}
    
    health = cache.health_check()
    return {
        "status": "healthy" if health["redis_connected"] else "degraded",
        "cache": health,
        "version": "1.0.0"
    }