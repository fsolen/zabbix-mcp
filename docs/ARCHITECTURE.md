# Zabbix MCP - Mimari Dokümantasyonu

Bu doküman, Zabbix MCP'nin iç çalışma mantığını ve teknik detaylarını açıklar.

## Genel Bakış

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           ZABBIX MCP SİSTEMİ                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                   │
│  │   Claude    │     │   REST      │     │   Worker    │                   │
│  │   Desktop   │     │   Client    │     │   Scanner   │                   │
│  └──────┬──────┘     └──────┬──────┘     └──────┬──────┘                   │
│         │                   │                   │                          │
│         │ MCP/SSE           │ HTTP/JSON         │ Internal                 │
│         ▼                   ▼                   ▼                          │
│  ┌──────────────────────────────────────────────────────────────────┐      │
│  │                        FastAPI Application                        │      │
│  ├───────────────┬───────────────┬───────────────┬─────────────────┤      │
│  │   MCP Routes  │   REST API    │   Health      │   Metrics       │      │
│  │   /mcp/sse    │   /analysis   │   /health     │   /metrics      │      │
│  └───────┬───────┴───────┬───────┴───────────────┴─────────────────┘      │
│          │               │                                                 │
│          ▼               ▼                                                 │
│  ┌────────────────────────────────┐                                       │
│  │        Tool Execution          │                                       │
│  │   (35+ Zabbix API tools)       │                                       │
│  └───────────────┬────────────────┘                                       │
│                  │                                                         │
│                  ▼                                                         │
│  ┌────────────────────────────────┐     ┌──────────────────────────┐      │
│  │       Cache Layer              │     │    Rate Limiter          │      │
│  │      (Redis Check)             │────▶│   (Token Bucket)         │      │
│  └───────────────┬────────────────┘     └───────────┬──────────────┘      │
│                  │                                  │                      │
│                  │ HIT: return cached               │ Acquire token        │
│                  │ MISS: query API                  │                      │
│                  ▼                                  ▼                      │
│  ┌────────────────────────────────────────────────────────────────┐       │
│  │                    Zabbix Client                                │       │
│  │   (httpx async, tenacity retry, HTTP/2)                        │       │
│  └────────────────────────────────────────────────────────────────┘       │
│                                                                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ JSON-RPC
                                    ▼
                    ┌───────────────────────────────┐
                    │       Zabbix Server           │
                    │    (Production Cluster)       │
                    └───────────────────────────────┘
```

---

## Bileşenler

### 1. FastAPI Application (`app/api.py`)

Ana HTTP sunucusu. Lifespan event'leri ile başlatılır.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.config = load_config()
    app.state.redis = await aioredis.from_url(...)
    app.state.cache = RedisCache(app.state.redis, ttl=900)
    
    yield  # Uygulama çalışır
    
    # Shutdown
    await app.state.redis.close()
```

### 2. MCP Routes (`app/mcp_routes.py`)

MCP protokolü SSE transport implementasyonu.

**Endpoints:**
- `GET /mcp/sse` - SSE stream başlatır
- `POST /mcp/sse` - JSON-RPC mesajları alır

**JSON-RPC Methods:**
```
initialize     → Server capabilities döner
tools/list     → Mevcut tool listesi (filtered)
tools/call     → Tool çalıştırır
notifications/initialized → Client ready
```

### 3. Zabbix Client (`app/zabbix_client.py`)

Async Zabbix API client. Her API methodu için ayrı fonksiyon.

**Özellikler:**
- httpx AsyncClient (HTTP/2 support)
- Connection pooling (max 20 connections)
- Tenacity retry (3 attempts, exponential backoff)
- Rate limiter integration

```python
class ZabbixClient:
    async def api_call(self, method: str, params: dict):
        await self.rate_limiter.acquire()  # Wait for token
        
        async with tenacity.retry(...):
            response = await self.client.post(
                self.url,
                json={"jsonrpc": "2.0", "method": method, ...}
            )
        
        return response.json()["result"]
```

### 4. Cache Layer (`app/cache.py`)

Redis-based distributed cache. Multi-pod deployment'ta paylaşılır.

```python
class RedisCache:
    async def get_or_fetch(self, key: str, fetch_fn: Callable):
        # 1. Cache'te ara
        cached = await self.redis.get(key)
        if cached:
            return json.loads(cached)
        
        # 2. Cache miss - fetch ve kaydet
        result = await fetch_fn()
        await self.redis.setex(key, self.ttl, json.dumps(result))
        
        return result
```

**Cache Key Format:**
```
zabbix:{method}:{sorted_params_hash}

Örnek:
zabbix:host.get:filter=status:0,limit=100
zabbix:problem.get:severity_min=3,recent=true
```

### 5. Rate Limiter (`app/rate_limiter.py`)

İki katmanlı rate limiting: distributed (Redis) + local (token bucket).

```python
class DistributedRateLimiter:
    async def acquire(self):
        # 1. Global rate check (Redis)
        key = f"ratelimit:{minute_bucket}"
        count = await self.redis.incr(key)
        
        if count > self.max_requests:
            raise RateLimitExceeded()
        
        # 2. Local token bucket (calls_per_second)
        await self.token_bucket.acquire()
```

**Token Bucket Algoritması:**
```
tokens = min(max_tokens, tokens + (elapsed * refill_rate))
if tokens >= 1:
    tokens -= 1
    return  # Proceed
else:
    wait = (1 - tokens) / refill_rate
    await asyncio.sleep(wait)
```

### 6. Worker Distributed (`app/worker_distributed.py`)

Background job: Grup bazlı COUNT sorguları ile istatistik toplama.

```python
async def scan_groups():
    groups = await zabbix.hostgroup_get()
    
    for group in groups:
        # COUNT sorgusu - item fetch ETMEZ
        host_count = await zabbix.get_hosts_count(group_id)
        item_count = await zabbix.get_items_count(group_id)
        trigger_count = await zabbix.get_trigger_counts(group_id)
        
        await cache.set(f"stats:{group_id}", {
            "hosts": host_count,
            "items": item_count,
            "triggers": trigger_count
        })
```

---

## Sorgu Akış Detayı

### Senaryo: `get_problems` tool çağrısı

```
1. Claude Desktop → POST /mcp/sse
   {
     "jsonrpc": "2.0",
     "method": "tools/call",
     "params": {"name": "get_problems", "arguments": {"severity_min": 3}}
   }

2. mcp_routes.py → execute_tool()
   - Tool tanımını bul
   - Arguments validate
   - Zabbix client'ı al (lazy init)

3. Cache Check
   key = "zabbix:problem.get:recent=true,severity_min=3"
   cached = await redis.get(key)
   
   if cached → 4a. Return cached
   else → 4b. Continue to API

4a. Cache HIT
   return json.loads(cached)

4b. Cache MISS → Rate Limit Check
   count = await redis.incr("ratelimit:2024-01-15T10:30")
   if count > 60 → 429 Rate Limit Error
   
   await token_bucket.acquire()  # Wait if needed

5. Zabbix API Call (with retry)
   response = await httpx.post(zabbix_url, json={
     "jsonrpc": "2.0",
     "method": "problem.get",
     "params": {
       "recent": true,
       "severities": [3, 4, 5],
       "output": "extend"
     },
     "auth": token
   })

6. Cache Store
   await redis.setex(key, 900, json.dumps(result))

7. Return to Claude
   {
     "jsonrpc": "2.0",
     "result": {
       "content": [{"type": "text", "text": "[{problem1}, {problem2}]"}]
     }
   }
```

---

## Konfigürasyon Hiyerarşisi

```
┌─────────────────────────────────────────────────────────────────┐
│                    Konfigürasyon Kaynakları                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. Defaults (config_loader.py)                                │
│     └─ Kod içinde tanımlı varsayılan değerler                  │
│                                                                 │
│  2. config.yaml (ConfigMap)                                    │
│     └─ Ana konfigürasyon dosyası                               │
│                                                                 │
│  3. Environment Variables                                       │
│     └─ ZABBIX_USER, ZABBIX_PASS (Secret'tan)                   │
│                                                                 │
│  Override Sırası: 1 → 2 → 3 (env en yüksek öncelik)           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Tool Filtering Mekanizması

### Tag Sistemi

Her tool'un bir veya daha fazla tag'i vardır:

```python
TOOLS = [
    {
        "name": "get_problems",
        "tags": ["trigger"],  # read-only, güvenli
        ...
    },
    {
        "name": "create_maintenance",
        "tags": ["maintenance", "write"],  # yazma işlemi
        ...
    },
    {
        "name": "execute_script",
        "tags": ["script", "write"],  # tehlikeli
        ...
    }
]
```

### Filtreleme Mantığı

```python
def get_filtered_tools():
    config = load_config()
    read_only = config.get("mode", {}).get("read_only", True)
    disabled_tags = set(config.get("disabled_tags", []))
    
    filtered = []
    for tool in TOOLS:
        tool_tags = set(tool.get("tags", []))
        
        # read_only mode'da "write" tag'li tool'ları gizle
        if read_only and "write" in tool_tags:
            continue
        
        # disabled_tags ile eşleşen tool'ları gizle
        if tool_tags & disabled_tags:
            continue
        
        filtered.append(tool)
    
    return filtered
```

### Örnek Senaryolar

| Config | Görünen Tool'lar |
|--------|-----------------|
| `read_only: true, disabled_tags: []` | Tüm read-only tool'lar |
| `read_only: false, disabled_tags: []` | TÜM tool'lar |
| `read_only: false, disabled_tags: ["script"]` | Script hariç tümü |
| `read_only: true, disabled_tags: ["maintenance"]` | Read-only, maintenance hariç |

---

## Error Handling

### Retry Stratejisi

```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException))
)
async def api_call(self, method, params):
    ...
```

**Timing:**
- 1. deneme: Hemen
- 2. deneme: 2 saniye sonra
- 3. deneme: 4 saniye sonra
- Toplam max bekleme: ~6 saniye

### Error Types

| Error | HTTP Code | Açıklama |
|-------|-----------|----------|
| Rate Limit | 429 | Çok fazla istek |
| Zabbix Auth | 401 | Token geçersiz/expired |
| Zabbix Timeout | 504 | API yanıt vermedi |
| Tool Not Found | 404 | Bilinmeyen tool adı |
| Invalid Args | 400 | Eksik/hatalı parametre |

---

## Performans Optimizasyonları

### 1. Connection Pooling
```python
# 20 concurrent connection, keep-alive
httpx.AsyncClient(
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    http2=True
)
```

### 2. Lazy Initialization
```python
# Zabbix client sadece ilk kullanımda oluşturulur
_client: Optional[ZabbixClient] = None

async def get_zabbix_client():
    global _client
    if _client is None:
        _client = ZabbixClient(...)
        await _client.login()
    return _client
```

### 3. COUNT Queries (Worker)
```python
# YANLIŞ: Tüm item'ları fetch etme
items = await zabbix.item_get(hostids=host_ids)  # 500k item!
count = len(items)

# DOĞRU: COUNT sorgusu
result = await zabbix.api_call("item.get", {
    "hostids": host_ids,
    "countOutput": True
})
count = int(result)  # Sadece sayı döner
```

### 4. Batch Processing
```python
# Host'ları 100'lük gruplar halinde işle
for chunk in chunked(host_ids, 100):
    results = await zabbix.item_get(hostids=chunk)
```

---

## Security

### Authentication
- Zabbix credentials Secret'ta (base64 encoded)
- Token memory'de tutulur, disk'e yazılmaz
- Token expired olursa auto-relogin

### Read-Only Mode
- Production'da varsayılan olarak aktif
- Yazma tool'ları API'den tamamen gizlenir
- Claude bu tool'ları göremez bile

### Network Security
- HTTPS (TLS 1.2+) zorunlu
- SSL certificate verification aktif
- Internal traffic pod-to-pod (ClusterIP)
