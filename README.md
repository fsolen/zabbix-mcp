# Zabbix MCP

Enterprise-grade Zabbix monitoring aracı ve MCP (Model Context Protocol) sunucusu. AI asistanlarının (Claude, GPT, vb.) Zabbix verilerine güvenli ve kontrollü erişimini sağlar.

**Desteklenen ölçek:** 2500+ host, 500k+ item, 100k+ trigger

## 🎯 Temel Özellikler

| Özellik | Açıklama |
|---------|----------|
| **MCP Protokolü** | Claude Desktop ve diğer AI asistanlarla native entegrasyon |
| **35+ Zabbix Tool** | Host, trigger, problem, maintenance, script ve daha fazlası |
| **Distributed Cache** | Redis tabanlı, multi-pod paylaşımlı cache |
| **Rate Limiting** | Zabbix API'yi korumak için akıllı istek sınırlama |
| **Read-Only Mode** | Production ortamda güvenli salt-okunur mod |
| **Tag-Based Filtering** | Tool'ları kategorilere göre etkinleştir/devre dışı bırak |
| **OpenShift Ready** | UBI9 image, restricted SCC uyumlu |

---

## 🔄 Çalışma Mantığı

### Sorgu Akış Diyagramı

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              SORGU AKIŞI                                      │
└──────────────────────────────────────────────────────────────────────────────┘

  ┌─────────┐      ┌─────────────┐      ┌─────────────┐      ┌─────────────┐
  │ Claude  │─────►│  MCP API    │─────►│ Cache Check │─────►│   Sonuç     │
  │ Desktop │      │  /mcp/sse   │      │   (Redis)   │      │   Dönüş     │
  └─────────┘      └─────────────┘      └──────┬──────┘      └─────────────┘
                                               │
                                    Cache MISS │
                                               ▼
                                        ┌─────────────┐
                                        │ Rate Limit  │
                                        │   Check     │
                                        └──────┬──────┘
                                               │
                                    Limit OK   │
                                               ▼
                                        ┌─────────────┐      ┌─────────────┐
                                        │  Zabbix     │─────►│ Cache'e     │
                                        │   API       │      │   Kaydet    │
                                        └─────────────┘      └─────────────┘
```

### Detaylı Akış Açıklaması

#### 1️⃣ İstek Gelir (MCP veya REST)
```
Claude: "Aktif problemleri listele"
   │
   ▼
POST /mcp/sse
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "get_problems",
    "arguments": {"severity_min": 3}
  }
}
```

#### 2️⃣ Cache Kontrolü (Redis)
```python
# Cache key oluşturulur
cache_key = "zabbix:problem.get:severity_min=3"

# Redis'te aranır
cached_result = await redis.get(cache_key)

if cached_result:
    # ✅ CACHE HIT - Direkt dön (Zabbix'e sorgu ATILMAZ)
    return json.loads(cached_result)
else:
    # ❌ CACHE MISS - Rate limit kontrolüne geç
    pass
```

**Cache TTL:** Varsayılan 900 saniye (15 dakika), ConfigMap'ten ayarlanabilir.

#### 3️⃣ Rate Limiting (Zabbix Koruması)
```python
# Distributed rate limiter (Redis-based)
rate_limit_key = f"ratelimit:{minute_bucket}"
current_count = await redis.incr(rate_limit_key)

if current_count > max_requests_per_minute:
    # ⛔ RATE LIMITED - 429 döner, Zabbix korunur
    raise HTTPException(429, "Rate limit exceeded")

# calls_per_second kontrolü (token bucket)
await rate_limiter.acquire()  # Bekler veya devam eder
```

**Rate Limit Değerleri:**
- `max_requests`: 60/dakika (ConfigMap)
- `calls_per_second`: 5 (ConfigMap)

#### 4️⃣ Zabbix API Çağrısı
```python
# Retry mekanizması ile (3 deneme, exponential backoff)
async with tenacity.retry(stop=stop_after_attempt(3)):
    response = await httpx_client.post(
        zabbix_url,
        json={"jsonrpc": "2.0", "method": "problem.get", ...}
    )
```

#### 5️⃣ Sonuç Cache'lenir ve Döner
```python
# Redis'e kaydet
await redis.setex(cache_key, ttl=900, value=json.dumps(result))

# Claude'a dön
return {"content": [{"type": "text", "text": json.dumps(result)}]}
```

---

### 🏗️ Mimari Diyagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            OPENSHIFT CLUSTER                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌──────────────┐                                                          │
│   │   Route      │◄──── HTTPS (edge TLS)                                    │
│   │ zabbix-mcp   │                                                          │
│   └──────┬───────┘                                                          │
│          │                                                                   │
│          ▼                                                                   │
│   ┌──────────────┐      ┌──────────────┐      ┌──────────────┐             │
│   │   API Pod    │      │   API Pod    │      │  Worker Pod  │             │
│   │  (FastAPI)   │      │  (FastAPI)   │      │  (Scanner)   │             │
│   │              │      │              │      │              │             │
│   │ • /mcp/sse   │      │ • /mcp/sse   │      │ • Group scan │             │
│   │ • /tools     │      │ • /tools     │      │ • Stats calc │             │
│   │ • /health    │      │ • /health    │      │ • COUNT query│             │
│   └──────┬───────┘      └──────┬───────┘      └──────┬───────┘             │
│          │                     │                     │                      │
│          └─────────────────────┴─────────────────────┘                      │
│                                │                                            │
│                                ▼                                            │
│                    ┌───────────────────────┐                               │
│                    │     Redis Master      │                               │
│                    │    (Cache + Rate)     │                               │
│                    │                       │                               │
│                    │ • Distributed cache   │                               │
│                    │ • Rate limit counters │                               │
│                    │ • Global stats store  │                               │
│                    └───────────────────────┘                               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                 │
                                 │ Zabbix API (rate limited)
                                 ▼
                    ┌───────────────────────┐
                    │     Zabbix Server     │
                    │    (Production)       │
                    │                       │
                    │ • 2500+ hosts         │
                    │ • 500k+ items         │
                    │ • 100k+ triggers      │
                    └───────────────────────┘
```

---

### 🛡️ Zabbix Koruma Mekanizmaları

| Mekanizma | Nasıl Çalışır | Ayar |
|-----------|---------------|------|
| **Redis Cache** | Aynı sorgu 15 dk cache'ten döner | `cache.ttl_sec: 900` |
| **Rate Limiting** | Dakikada max 60 istek | `rate_limit.max_requests: 60` |
| **Calls/Second** | Saniyede max 5 API çağrısı | `rate_limit.calls_per_second: 5` |
| **COUNT Queries** | Worker item fetch yerine COUNT kullanır | Otomatik |
| **Read-Only Mode** | Yazma işlemlerini engeller | `mode.read_only: true` |
| **Retry + Backoff** | Hata durumunda akıllı tekrar deneme | 3 deneme, exponential |

---

## 🤖 MCP Entegrasyonu (Claude Desktop)

### Claude Desktop Konfigürasyonu

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "zabbix": {
      "url": "https://zabbix-mcp.apps.cluster.example.com/mcp/sse",
      "transport": "sse"
    }
  }
}
```

### Örnek Claude Konuşmaları

```
👤 User: "Zabbix'te kaç tane aktif problem var?"

🤖 Claude: [get_problems tool'unu çağırır]
   "Şu anda 23 aktif problem var:
    - 5 High severity (trigger down)
    - 12 Average severity (disk space)
    - 6 Warning severity (CPU usage)"

👤 User: "web-server-01 host'unu maintenance'a al, 2 saat"

🤖 Claude: [create_maintenance tool'unu çağırır]
   "web-server-01 için 2 saatlik maintenance oluşturuldu.
    Maintenance ID: 12345
    Bitiş: 2024-01-15 16:00:00"
```

---

## 🔧 Mevcut Tool'lar (35+)

### 📊 Sistem & İstatistik
| Tool | Açıklama | Tag |
|------|----------|-----|
| `get_api_info` | Zabbix API versiyon bilgisi | system |
| `get_global_stats` | Toplam host/item/trigger sayıları | system |
| `get_queue` | İşlenmeyi bekleyen item kuyruğu | system |

### 🖥️ Host Yönetimi
| Tool | Açıklama | Tag |
|------|----------|-----|
| `get_hosts` | Host listesi (filter, limit, search) | host |
| `get_host_details` | Tek host detayı | host |
| `get_host_interfaces` | Host interface bilgileri | host |
| `host_enable` | Host'u etkinleştir | host, write |
| `host_disable` | Host'u devre dışı bırak | host, write |

### ⚠️ Trigger & Problem
| Tool | Açıklama | Tag |
|------|----------|-----|
| `get_triggers` | Trigger listesi | trigger |
| `get_trigger_details` | Tek trigger detayı | trigger |
| `get_problems` | Aktif problemler | trigger |
| `get_events` | Event geçmişi | trigger |
| `acknowledge_event` | Problem acknowledge | trigger, write |

### 📈 Metrik & History
| Tool | Açıklama | Tag |
|------|----------|-----|
| `get_items` | Item listesi | item |
| `get_item_details` | Tek item detayı | item |
| `get_history` | Item history verileri | item |
| `get_trends` | Trend verileri (hourly/daily) | item |

### 🔧 Maintenance
| Tool | Açıklama | Tag |
|------|----------|-----|
| `get_maintenances` | Maintenance listesi | maintenance |
| `create_maintenance` | Yeni maintenance oluştur | maintenance, write |
| `delete_maintenance` | Maintenance sil | maintenance, write |

### 📜 Script & Template
| Tool | Açıklama | Tag |
|------|----------|-----|
| `get_scripts` | Script listesi | script |
| `execute_script` | Host üzerinde script çalıştır | script, write |
| `get_templates` | Template listesi | template |

### 👥 Kullanıcı & Grup
| Tool | Açıklama | Tag |
|------|----------|-----|
| `get_users` | Kullanıcı listesi | user |
| `get_usergroups` | Kullanıcı grupları | user |
| `get_hostgroups` | Host grupları | host |

---

## ⚙️ Konfigürasyon

### ConfigMap Yapısı (config.yaml)

```yaml
# Çalışma modu
mode:
  read_only: true              # true: yazma tool'ları gizlenir

# Zabbix bağlantısı
zabbix:
  url: "https://zabbix.example.com/api_jsonrpc.php"
  timeout: 60                  # API timeout (saniye)
  max_retries: 3               # Retry sayısı
  verify_ssl: true             # SSL sertifika doğrulama
  skip_version_check: false

# Redis bağlantısı
redis:
  url: "redis://redis-master:6379/0"

# Cache ayarları
cache:
  ttl_sec: 900                 # Cache süresi (15 dakika)

# Sorgu limitleri
limits:
  max_hosts_per_query: 100     # Tek sorguda max host
  max_items_per_query: 5000    # Tek sorguda max item
  concurrent_groups: 3         # Paralel grup işleme

# Rate limiting (Zabbix koruması)
rate_limit:
  enabled: true
  max_requests: 60             # Dakikada max istek
  window_minutes: 1
  calls_per_second: 5          # Zabbix API rate limit

# Background worker
worker:
  scan_interval_sec: 600       # Scan aralığı (10 dakika)
  metrics_port: 9090

# Logging
logging:
  level: INFO                  # DEBUG, INFO, WARNING, ERROR

# Tool filtreleme (tag bazlı)
disabled_tags: []
# Örnek: ["write", "maintenance", "script"]
```

### Environment Variables

| Variable | Açıklama | Kaynak |
|----------|----------|--------|
| `ZABBIX_USER` | Zabbix API kullanıcısı | Secret |
| `ZABBIX_PASS` | Zabbix API şifresi | Secret |
| `CONFIG_PATH` | Config dosyası yolu | ConfigMap |
| `WORKER_ID` | Worker pod identifier | Downward API |

---

## 🚀 Deployment

### OpenShift Quick Start

```bash
# 1. Login
oc login https://api.cluster.example.com:6443

# 2. Proje oluştur
oc new-project zabbix-mcp

# 3. Secret oluştur
oc create secret generic zabbix-mcp-secret \
  --from-literal=ZABBIX_USER=api_user \
  --from-literal=ZABBIX_PASS=your_password

# 4. Deploy
oc apply -k openshift/

# 5. Build başlat
oc start-build zabbix-mcp --follow

# 6. Route URL'ini al
oc get route zabbix-mcp-api -o jsonpath='{.spec.host}'
```

### Local Development

```bash
# Virtual environment
python -m venv venv && source venv/bin/activate

# Dependencies
pip install -r requirements.txt

# Redis başlat
docker run -d -p 6379:6379 redis:7-alpine \
  --protected-mode no --appendonly yes

# Environment
export ZABBIX_USER="api_user"
export ZABBIX_PASS="your_password"
export CONFIG_PATH="config.yaml"

# API başlat
uvicorn app.api:app --reload --port 8080
```

---

## 📊 API Endpoints

| Endpoint | Method | Açıklama |
|----------|--------|----------|
| `/health` | GET | Liveness probe |
| `/ready` | GET | Readiness probe |
| `/metrics` | GET | Prometheus metrics |
| `/tools` | GET | Mevcut tool listesi |
| `/mcp/sse` | GET | MCP SSE stream |
| `/mcp/sse` | POST | MCP JSON-RPC messages |
| `/analysis` | GET | Grup analizleri |
| `/status` | GET | Sistem durumu |

---

## 📁 Dosya Yapısı

```
zabbix-mcp/
├── app/
│   ├── api.py                 # FastAPI main application
│   ├── mcp_routes.py          # MCP SSE routes & 35+ tools
│   ├── zabbix_client.py       # Async Zabbix API client
│   ├── worker_distributed.py  # Background group scanner
│   ├── cache.py               # Redis cache wrapper
│   ├── config_loader.py       # YAML config + defaults
│   └── rate_limiter.py        # Distributed rate limiter
├── openshift/
│   └── k8s/
│       ├── configmap.yaml     # Ana konfigürasyon
│       ├── secret.yaml        # Credentials
│       ├── api-deployment.yaml
│       ├── worker-deployment.yaml
│       ├── redis-statefulset.yaml
│       └── route.yaml
├── k8s/                       # Vanilla Kubernetes manifests
├── config.yaml                # Local development config
├── requirements.txt
├── Dockerfile
└── README.md
```

---

## 🔍 Troubleshooting

### Cache çalışmıyor
```bash
# Redis bağlantısı kontrol
oc exec -it redis-master-0 -- redis-cli ping

# Cache key'leri listele
oc exec -it redis-master-0 -- redis-cli keys "zabbix:*"
```

### Rate limit hatası (429)
```yaml
# ConfigMap'te rate_limit değerlerini artır
rate_limit:
  max_requests: 120
  calls_per_second: 10
```

### Tool görünmüyor
```yaml
# disabled_tags ve mode.read_only kontrol et
mode:
  read_only: false  # write tool'ları için
disabled_tags: []   # boş olmalı
```

---

## 📄 Lisans

MIT License

