# Zabbix MCP (Monitoring Control Plane)

Zabbix cluster için tasarlanmış analiz ve monitoring aracı. 2500+ host ve 500k+ item destekler.

## Özellikler

- **Async I/O**: httpx ile paralel API çağrıları
- **Redis Cache**: Multi-pod deployment için distributed cache
- **Rate Limiting**: Zabbix API koruması (distributed + local fallback)
- **Pagination**: Büyük item setleri için otomatik sayfalama
- **Auto-scaling**: KEDA ile Redis stream-based scaling
- **Metrics**: Prometheus metrics endpoint
- **OpenShift Ready**: Restricted SCC uyumlu

## Mimari

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Zabbix    │◄────│   Workers   │────►│    Redis    │
│   Server    │     │  (2-10 pod) │     │   Cache     │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
                    ┌─────────────┐             │
                    │   API       │◄────────────┘
                    │  (2-5 pod)  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │   Route     │
                    │  (HTTPS)    │
                    └─────────────┘
```

## Gereksinimler

- Python 3.11+
- Redis 7+
- OpenShift 4.x veya Kubernetes 1.25+
- (Opsiyonel) KEDA 2.x

## Hızlı Başlangıç

### Local Development

```bash
# Virtual environment
python -m venv venv
source venv/bin/activate

# Dependencies
pip install -r requirements.txt

# Redis başlat
docker run -d -p 6379:6379 redis:7-alpine

# Environment
export ZABBIX_USER="api_user"
export ZABBIX_PASS="your_password"
export CONFIG_PATH="config.yaml"

# API başlat
uvicorn app.api:app --reload --port 8080

# Worker başlat (ayrı terminal)
python -m app.worker_distributed
```

### Container Build

```bash
# Podman/Docker ile build
./build.sh -t v1.0.0

# Registry'ye push
./build.sh -r registry.example.com/myproject -t v1.0.0 --push

# OpenShift BuildConfig ile
./build.sh --openshift
```

## OpenShift Deployment

### Ön Koşullar

```bash
# OpenShift'e login
oc login https://api.cluster.example.com:6443

# Proje oluştur
oc new-project zabbix-mcp
```

### Deploy

```bash
# Tüm kaynakları deploy et
./deploy-openshift.sh apply

# Veya manuel
oc apply -k openshift/
```

### Konfigürasyon

1. **Secret oluştur** (credentials için):
```bash
oc create secret generic zabbix-mcp-secret \
  --from-literal=ZABBIX_USER=api_user \
  --from-literal=ZABBIX_PASS=your_password \
  -n zabbix-mcp
```

2. **ConfigMap güncelle** (Zabbix URL):
```bash
oc edit configmap zabbix-mcp-config -n zabbix-mcp
```

3. **Route host güncelle**:
```bash
oc patch route zabbix-mcp-api \
  -p '{"spec":{"host":"zabbix-mcp.apps.your-cluster.com"}}' \
  -n zabbix-mcp
```

### Yönetim Komutları

```bash
# Status
./deploy-openshift.sh status

# Logs
./deploy-openshift.sh logs api
./deploy-openshift.sh logs worker

# Build trigger
./deploy-openshift.sh build

# Sil
./deploy-openshift.sh delete
```

## Kubernetes Deployment (Vanilla)

```bash
# Namespace oluştur
kubectl create namespace zabbix-mcp

# Secret oluştur
kubectl create secret generic zabbix-mcp-secret \
  --from-literal=ZABBIX_USER=api_user \
  --from-literal=ZABBIX_PASS=your_password \
  -n zabbix-mcp

# Deploy
kubectl apply -k k8s/

# KEDA varsa (opsiyonel)
kubectl apply -f k8s/keda.yaml
```

## API Endpoints

| Endpoint | Method | Açıklama |
|----------|--------|----------|
| `/health` | GET | Liveness probe |
| `/ready` | GET | Readiness probe (Redis bağlantısı) |
| `/metrics` | GET | Prometheus metrics |
| `/analysis` | GET | Tüm grup analizleri |
| `/analysis/{group}` | GET | Belirli grup analizi |
| `/status` | GET | Sistem durumu |

### Örnek Çıktı

```json
{
  "group": "Linux Servers",
  "metrics": {
    "hosts": 150,
    "items": 45000,
    "unsupported": 120,
    "triggers": 3000,
    "active_triggers": 15
  },
  "analysis": {
    "noise_score": 0.05
  },
  "recommendations": ["healthy"]
}
```

## Konfigürasyon

### config.yaml

```yaml
mode:
  read_only: true

zabbix:
  url: "https://zabbix.example.com/api_jsonrpc.php"
  timeout: 30
  max_retries: 3

redis:
  url: "redis://redis-master:6379/0"

cache:
  ttl_sec: 600

limits:
  max_hosts_per_query: 50      # Host chunk size
  max_items_per_query: 1000    # Item pagination batch
  rate_limit_calls: 10         # API calls per second
  rate_limit_period: 1
  concurrent_groups: 5         # Parallel group processing

worker:
  scan_interval_sec: 30
  metrics_port: 9090
```

### Environment Variables

| Variable | Açıklama | Default |
|----------|----------|---------|
| `ZABBIX_USER` | Zabbix API kullanıcısı | - |
| `ZABBIX_PASS` | Zabbix API şifresi | - |
| `CONFIG_PATH` | Config dosyası yolu | `config.yaml` |
| `REDIS_URL` | Redis bağlantı URL'i | - |
| `WORKER_ID` | Worker pod identifier | `worker-0` |

## Monitoring

### Prometheus Metrics

```
# API Metrics
http_requests_total{method, endpoint, status}
cache_items_total

# Zabbix Client Metrics
zabbix_api_calls_total{method, status}
zabbix_api_latency_seconds{method}

# Worker Metrics
scan_duration_seconds
groups_processed_total{status}
hosts_scanned_total
items_scanned_total
```

### Grafana Dashboard

Import `monitoring/grafana-dashboard.json` (TODO)

## Scaling

### Horizontal Pod Autoscaler

- **API**: 2-5 replicas (CPU/Memory based)
- **Worker**: 2-10 replicas (KEDA Redis stream lag based)

### Performans Tahminleri

| Host Sayısı | Item Sayısı | Worker | Scan Süresi |
|-------------|-------------|--------|-------------|
| 500 | 100k | 2 | ~30s |
| 1000 | 250k | 3 | ~1m |
| 2500 | 500k | 5 | ~2-3m |

## Troubleshooting

### Redis bağlantı hatası

```bash
# Redis pod kontrol
oc get pods -l app.kubernetes.io/name=redis

# Redis logs
oc logs -l app.kubernetes.io/name=redis

# Redis CLI test
oc exec -it redis-master-0 -- redis-cli ping
```

### Zabbix API timeout

1. `config.yaml`'da timeout değerini artır
2. Rate limit değerini düşür
3. Worker replica sayısını azalt

### Pod CrashLoopBackOff

```bash
# Logs kontrol
oc logs <pod-name> --previous

# Events kontrol
oc get events --sort-by='.lastTimestamp'

# Describe pod
oc describe pod <pod-name>
```

## Dosya Yapısı

```
zabbix-mcp/
├── app/
│   ├── __init__.py
│   ├── api.py              # FastAPI endpoints
│   ├── analyzer.py         # Analysis logic
│   ├── cache.py            # Redis/TTL cache
│   ├── chunk.py            # List chunking utility
│   ├── config_loader.py    # YAML config + env
│   ├── rate_limiter.py     # Distributed rate limiter
│   ├── worker_distributed.py  # Async worker
│   └── zabbix_client.py    # Async Zabbix API client
├── k8s/                    # Kubernetes manifests
│   ├── kustomization.yaml
│   ├── configmap.yaml
│   ├── secret.yaml
│   ├── redis.yaml
│   ├── api.yaml
│   ├── worker.yaml
│   ├── pdb.yaml
│   └── keda.yaml
├── openshift/              # OpenShift overlay
│   ├── kustomization.yaml
│   ├── project.yaml
│   ├── imagestream.yaml
│   ├── buildconfig.yaml
│   ├── route.yaml
│   └── patches.yaml
├── config.yaml
├── requirements.txt
├── Dockerfile
├── build.sh
├── deploy-openshift.sh
└── README.md
```

