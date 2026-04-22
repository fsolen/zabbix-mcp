import asyncio
import signal
import os
import time
import structlog
from aiohttp import web
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

from .config_loader import load_config
from .cache import RedisCache
from .chunk import chunk_list
from .analyzer import analyze
from .rate_limiter import DistributedRateLimiter
from .zabbix_client import ZabbixClient

logger = structlog.get_logger()

# Metrics
scan_duration = Histogram('scan_duration_seconds', 'Duration of full scan')
groups_processed = Counter('groups_processed_total', 'Total groups processed', ['status'])
hosts_scanned = Gauge('hosts_scanned_total', 'Total hosts in last scan')
items_scanned = Gauge('items_scanned_total', 'Total items in last scan')
groups_skipped = Counter('groups_skipped_total', 'Groups skipped due to no changes')
api_calls_saved = Counter('api_calls_saved_total', 'API calls saved by incremental update')

running = True
worker_id = os.getenv("WORKER_ID", "worker-0")


def stop(sig, frame):
    global running
    logger.info("shutdown_signal_received", signal=sig)
    running = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


async def process_group(client: ZabbixClient, group: dict, cfg: dict) -> dict:
    """Process a single host group using COUNT queries (fast, no data transfer)"""
    gid = group["groupid"]
    gname = group["name"]
    
    try:
        hosts = await client.get_hosts(gid)
        
        # Empty groups - still cache them with zero metrics
        if not hosts:
            return {
                "group": gname,
                "metrics": {
                    "hosts": 0,
                    "items": 0,
                    "unsupported": 0,
                    "triggers": 0,
                    "active_triggers": 0
                },
                "analysis": {"noise_score": 0},
                "recommendations": ["empty group"]
            }
        
        host_ids = [h["hostid"] for h in hosts]
        
        # Use COUNT queries instead of fetching all items
        total_items = await client.get_items_count(host_ids)
        unsupported_items = await client.get_unsupported_count(host_ids)
        
        # Get trigger counts (not data)
        total_triggers, active_triggers = await client.get_trigger_counts(host_ids)
        
        # Calculate metrics
        noise = active_triggers / total_triggers if total_triggers > 0 else 0
        unsupported_pct = unsupported_items / total_items if total_items > 0 else 0
        
        recs = []
        if unsupported_pct > 0.1:
            recs.append("fix unsupported items")
        if noise > 0.3:
            recs.append("reduce trigger noise")
        if not recs:
            recs.append("healthy")
        
        result = {
            "group": gname,
            "metrics": {
                "hosts": len(hosts),
                "items": total_items,
                "unsupported": unsupported_items,
                "triggers": total_triggers,
                "active_triggers": active_triggers
            },
            "analysis": {"noise_score": round(noise, 2)},
            "recommendations": recs
        }
        
        groups_processed.labels(status="success").inc()
        
        logger.info("group_processed", 
                    group=gname, 
                    hosts=len(hosts), 
                    items=total_items,
                    triggers=total_triggers)
        
        return result
        
    except Exception as e:
        groups_processed.labels(status="error").inc()
        logger.error("group_processing_error", group=gname, error=str(e))
        return None


async def should_rescan_group(client: ZabbixClient, cache: RedisCache, group: dict) -> bool:
    """Check if group needs rescan based on host count change"""
    gid = group["groupid"]
    gname = group["name"]
    
    # Get cached data
    cached = cache.get(f"analysis:{gname}")
    if not cached:
        return True  # No cache, need full scan
    
    # Quick check: compare host count
    try:
        hosts = await client.call("hostgroup.get", {
            "groupids": gid,
            "selectHosts": "count"
        })
        if hosts:
            current_count = int(hosts[0].get("hosts", 0))
            cached_count = cached.get("metrics", {}).get("hosts", 0)
            
            if current_count != cached_count:
                logger.debug("group_changed", group=gname, 
                           old_count=cached_count, new_count=current_count)
                return True
    except Exception as e:
        logger.warning("host_count_check_failed", group=gname, error=str(e))
        return True  # Rescan on error
    
    # Check cache age
    cache_age = cache.get_age(f"analysis:{gname}")
    max_age = 1800  # Force rescan every 30 minutes regardless
    if cache_age and cache_age > max_age:
        return True
    
    groups_skipped.inc()
    api_calls_saved.inc(3)  # Saved ~3 API calls per group
    return False


async def run_scan(client: ZabbixClient, cache: RedisCache, cfg: dict):
    """Run a smart incremental scan of all groups"""
    with scan_duration.time():
        groups = await client.get_groups()
        logger.info("scan_started", groups=len(groups))
        
        concurrent_limit = cfg["limits"].get("concurrent_groups", 3)
        semaphore = asyncio.Semaphore(concurrent_limit)
        
        async def smart_process(group):
            async with semaphore:
                # Check if rescan needed
                if not await should_rescan_group(client, cache, group):
                    logger.debug("group_skipped", group=group["name"])
                    return None
                return await process_group(client, group, cfg)
        
        # Process groups concurrently
        tasks = [smart_process(g) for g in groups]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Store results in cache
        total_hosts = 0
        total_items = 0
        processed = 0
        
        for result in results:
            if result and isinstance(result, dict):
                cache.set(f"analysis:{result['group']}", result)
                total_hosts += result["metrics"]["hosts"]
                total_items += result["metrics"]["items"]
                processed += 1
        
        # Get and store global stats (unique counts from Zabbix)
        try:
            global_stats = await client.get_global_stats()
            cache.set("global:stats", global_stats)
            logger.info("global_stats_updated", **global_stats)
        except Exception as e:
            logger.warning("global_stats_failed", error=str(e))
        
        hosts_scanned.set(total_hosts)
        items_scanned.set(total_items)
        
        logger.info("scan_completed", 
                    groups=len(groups), 
                    processed=processed,
                    skipped=len(groups) - processed,
                    hosts=total_hosts, 
                    items=total_items)


async def health_server(cfg: dict):
    """Simple health check server for Kubernetes probes"""
    port = cfg["worker"].get("metrics_port", 9090)
    
    async def health(request):
        return web.Response(text='{"status":"ok"}', content_type="application/json")
    
    async def ready(request):
        return web.Response(text='{"status":"ready"}', content_type="application/json")
    
    async def metrics(request):
        return web.Response(
            body=generate_latest(),
            content_type=CONTENT_TYPE_LATEST
        )
    
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/ready", ready)
    app.router.add_get("/metrics", metrics)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("health_server_started", port=port)
    
    return runner


async def main():
    cfg = load_config()
    
    # Initialize components
    cache = RedisCache(
        url=cfg["redis"]["url"],
        ttl=cfg["cache"]["ttl_sec"]
    )
    
    rate_limiter = DistributedRateLimiter(
        redis_url=cfg["redis"]["url"],
        calls_per_second=cfg["limits"]["rate_limit_calls"]
    )
    
    client = ZabbixClient(cfg["zabbix"], rate_limiter)
    await client.login()
    
    # Start health server
    health_runner = await health_server(cfg)
    
    scan_interval = cfg["worker"]["scan_interval_sec"]
    
    logger.info("worker_started", 
                worker_id=worker_id, 
                scan_interval=scan_interval)
    
    try:
        while running:
            await run_scan(client, cache, cfg)
            
            # Sleep with interrupt check
            for _ in range(scan_interval):
                if not running:
                    break
                await asyncio.sleep(1)
    
    finally:
        logger.info("worker_shutting_down")
        await client.logout()
        await client.close()
        await health_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())