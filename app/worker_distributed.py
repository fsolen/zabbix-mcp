import asyncio
import signal
import os
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

running = True
worker_id = os.getenv("WORKER_ID", "worker-0")


def stop(sig, frame):
    global running
    logger.info("shutdown_signal_received", signal=sig)
    running = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


async def process_group(client: ZabbixClient, group: dict, cfg: dict) -> dict:
    """Process a single host group"""
    gid = group["groupid"]
    gname = group["name"]
    
    try:
        hosts = await client.get_hosts(gid)
        if not hosts:
            return None
        
        host_ids = [h["hostid"] for h in hosts]
        all_items = []
        all_triggers = []
        
        # Chunk hosts for API calls
        chunk_size = cfg["limits"]["max_hosts_per_query"]
        item_limit = cfg["limits"]["max_items_per_query"]
        
        for h_chunk in chunk_list(host_ids, chunk_size):
            items = await client.get_items_paginated(h_chunk, item_limit)
            triggers = await client.get_triggers(h_chunk)
            all_items.extend(items)
            all_triggers.extend(triggers)
        
        result = analyze(gname, hosts, all_items, all_triggers)
        groups_processed.labels(status="success").inc()
        
        logger.info("group_processed", 
                    group=gname, 
                    hosts=len(hosts), 
                    items=len(all_items),
                    triggers=len(all_triggers))
        
        return result
        
    except Exception as e:
        groups_processed.labels(status="error").inc()
        logger.error("group_processing_error", group=gname, error=str(e))
        return None


async def run_scan(client: ZabbixClient, cache: RedisCache, cfg: dict):
    """Run a full scan of all groups"""
    with scan_duration.time():
        groups = await client.get_groups()
        logger.info("scan_started", groups=len(groups))
        
        concurrent_limit = cfg["limits"].get("concurrent_groups", 5)
        semaphore = asyncio.Semaphore(concurrent_limit)
        
        async def limited_process(group):
            async with semaphore:
                return await process_group(client, group, cfg)
        
        # Process groups concurrently
        tasks = [limited_process(g) for g in groups]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Store results in cache
        total_hosts = 0
        total_items = 0
        
        for result in results:
            if result and isinstance(result, dict):
                cache.set(f"analysis:{result['group']}", result)
                total_hosts += result["metrics"]["hosts"]
                total_items += result["metrics"]["items"]
        
        hosts_scanned.set(total_hosts)
        items_scanned.set(total_items)
        
        logger.info("scan_completed", 
                    groups=len(groups), 
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