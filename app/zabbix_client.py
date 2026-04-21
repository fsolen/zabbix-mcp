import httpx
import asyncio
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from prometheus_client import Counter, Histogram

logger = structlog.get_logger()

# Metrics
api_calls = Counter('zabbix_api_calls_total', 'Total Zabbix API calls', ['method', 'status'])
api_latency = Histogram('zabbix_api_latency_seconds', 'Zabbix API call latency', ['method'])


class ZabbixClient:
    def __init__(self, cfg, rate_limiter=None):
        self.url = cfg["url"]
        self.user = cfg.get("user")
        self.password = cfg.get("password")
        self.timeout = cfg.get("timeout", 30)
        self.auth = None
        self.id = 0
        self.rate_limiter = rate_limiter
        self.client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(self.timeout),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50)
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException))
    )
    async def _post(self, payload):
        if self.rate_limiter:
            await self.rate_limiter.acquire()
        
        method = payload.get("method", "unknown")
        with api_latency.labels(method=method).time():
            try:
                r = await self.client.post(self.url, json=payload)
                r.raise_for_status()
                api_calls.labels(method=method, status="success").inc()
                
                # Check for empty response
                if not r.content:
                    logger.error("zabbix_empty_response", method=method, url=self.url)
                    raise Exception(f"Empty response from Zabbix API: {self.url}")
                
                # Try to parse JSON
                try:
                    return r.json()
                except Exception as e:
                    logger.error("zabbix_invalid_json", 
                               method=method, 
                               content=r.text[:500],
                               status_code=r.status_code)
                    raise Exception(f"Invalid JSON from Zabbix: {r.text[:200]}")
                    
            except Exception as e:
                api_calls.labels(method=method, status="error").inc()
                logger.error("zabbix_api_error", method=method, error=str(e))
                raise

    async def call(self, method, params):
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "auth": self.auth,
            "id": self.id
        }
        self.id += 1
        result = await self._post(payload)
        
        if "error" in result:
            raise Exception(f"Zabbix API error: {result['error']}")
        
        return result.get("result", [])

    async def login(self):
        self.auth = await self.call("user.login", {
            "username": self.user,
            "password": self.password
        })
        logger.info("zabbix_login_success")

    async def logout(self):
        if self.auth:
            await self.call("user.logout", [])
            self.auth = None

    async def close(self):
        await self.client.aclose()

    async def get_groups(self):
        return await self.call("hostgroup.get", {"output": ["groupid", "name"]})

    async def get_hosts(self, gid):
        return await self.call("host.get", {
            "groupids": gid,
            "output": ["hostid", "name"]
        })

    async def get_items_paginated(self, hostids, batch_size=5000):
        """Item'ları çeker - MAX_ITEMS limiti ile"""
        MAX_ITEMS = 50000  # Tek sorguda max item
        items = []
        offset = 0
        
        while offset < MAX_ITEMS:
            batch = await self.call("item.get", {
                "hostids": hostids,
                "output": ["itemid", "state", "status"],  # Minimal output
                "limit": batch_size,
                "offset": offset
            })
            
            if not batch:
                break
            
            items.extend(batch)
            offset += len(batch)
            
            logger.debug("items_fetched", count=len(batch), total=len(items))
            
            if len(batch) < batch_size:
                break
        
        if offset >= MAX_ITEMS:
            logger.warning("items_limit_reached", hostids=len(hostids), limit=MAX_ITEMS)
        
        return items

    async def get_items_count(self, hostids):
        """Item sayısını al (çekmeden)"""
        result = await self.call("item.get", {
            "hostids": hostids,
            "countOutput": True
        })
        return int(result) if result else 0

    async def get_unsupported_count(self, hostids):
        """Unsupported item sayısını al"""
        result = await self.call("item.get", {
            "hostids": hostids,
            "filter": {"state": 1},
            "countOutput": True
        })
        return int(result) if result else 0

    async def get_triggers(self, hostids):
        """Get triggers with minimal output and limit"""
        return await self.call("trigger.get", {
            "hostids": hostids,
            "output": ["triggerid", "value"],  # Minimal - sadece id ve durum
            "limit": 10000,  # Safety limit
            "skipDependent": True,  # Skip dependent triggers
            "only_true": False
        })

    async def get_trigger_counts(self, hostids):
        """Get total and active trigger counts"""
        total = await self.call("trigger.get", {
            "hostids": hostids,
            "countOutput": True
        })
        
        active = await self.call("trigger.get", {
            "hostids": hostids,
            "filter": {"value": 1},
            "countOutput": True
        })
        
        return int(total) if total else 0, int(active) if active else 0