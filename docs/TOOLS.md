# Zabbix MCP - Tool Referansı

Bu doküman, Zabbix MCP'de mevcut olan tüm tool'ları ve kullanım detaylarını açıklar.

## Tool Kategorileri

| Kategori | Tag | Açıklama |
|----------|-----|----------|
| Sistem | `system` | API bilgisi, global istatistikler |
| Host | `host` | Host sorgulama ve yönetimi |
| Trigger | `trigger` | Trigger, problem, event işlemleri |
| Item | `item` | Item, history, trend verileri |
| Maintenance | `maintenance` | Maintenance yönetimi |
| Script | `script` | Script çalıştırma |
| Template | `template` | Template sorguları |
| User | `user` | Kullanıcı ve grup sorguları |
| Yazma | `write` | Değişiklik yapan işlemler |

---

## 📊 Sistem Tool'ları

### get_api_info

Zabbix API versiyonu ve sunucu bilgisi.

**Parametreler:** Yok

**Örnek Yanıt:**
```json
{
  "version": "6.4.0",
  "server": "Zabbix Production"
}
```

---

### get_global_stats

Zabbix ortamının genel istatistikleri.

**Parametreler:** Yok

**Örnek Yanıt:**
```json
{
  "total_hosts": 2547,
  "enabled_hosts": 2450,
  "disabled_hosts": 97,
  "total_items": 512340,
  "enabled_items": 498200,
  "disabled_items": 14140,
  "unsupported_items": 1250,
  "total_triggers": 89500,
  "enabled_triggers": 87200,
  "problem_triggers": 23,
  "total_users": 45,
  "total_hostgroups": 128
}
```

---

### get_queue

İşlenmeyi bekleyen item kuyruğu bilgisi.

**Parametreler:** Yok

**Örnek Yanıt:**
```json
{
  "queue_count": 15,
  "items_delayed_by": {
    "5-10_seconds": 8,
    "10-30_seconds": 5,
    "30-60_seconds": 2,
    "1-5_minutes": 0,
    "5+_minutes": 0
  }
}
```

---

## 🖥️ Host Tool'ları

### get_hosts

Host listesi sorgulama.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `group_name` | string | ❌ | Host grup adı ile filtrele |
| `host_pattern` | string | ❌ | Host adı pattern (wildcard) |
| `status` | string | ❌ | `enabled`, `disabled`, `all` |
| `limit` | integer | ❌ | Max sonuç sayısı (default: 100) |

**Örnek Kullanım:**
```json
{
  "name": "get_hosts",
  "arguments": {
    "group_name": "Linux Servers",
    "status": "enabled",
    "limit": 50
  }
}
```

**Örnek Yanıt:**
```json
[
  {
    "hostid": "10084",
    "host": "web-server-01",
    "name": "Web Server 01",
    "status": "0",
    "available": "1"
  }
]
```

---

### get_host_details

Tek bir host'un detaylı bilgisi.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `host_id` | string | ⚠️ | Host ID |
| `host_name` | string | ⚠️ | Host adı (host_id yoksa) |

**Örnek Kullanım:**
```json
{
  "name": "get_host_details",
  "arguments": {
    "host_name": "web-server-01"
  }
}
```

---

### get_host_interfaces

Host'un interface bilgileri (IP, port, type).

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `host_id` | string | ✅ | Host ID |

**Örnek Yanıt:**
```json
[
  {
    "interfaceid": "1",
    "hostid": "10084",
    "main": "1",
    "type": "1",
    "ip": "192.168.1.100",
    "dns": "web-server-01.local",
    "port": "10050"
  }
]
```

---

### host_enable ⚠️ WRITE

Host'u etkinleştirir.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `host_id` | string | ✅ | Host ID |

**Tag:** `host`, `write`

---

### host_disable ⚠️ WRITE

Host'u devre dışı bırakır.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `host_id` | string | ✅ | Host ID |

**Tag:** `host`, `write`

---

### get_hostgroups

Host grupları listesi.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `filter_name` | string | ❌ | Grup adı ile filtrele |

**Örnek Yanıt:**
```json
[
  {
    "groupid": "2",
    "name": "Linux servers",
    "hosts": 150
  },
  {
    "groupid": "5",
    "name": "Windows servers",
    "hosts": 85
  }
]
```

---

## ⚠️ Trigger & Problem Tool'ları

### get_triggers

Trigger listesi sorgulama.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `host_ids` | array | ❌ | Host ID listesi |
| `group_ids` | array | ❌ | Grup ID listesi |
| `only_problems` | boolean | ❌ | Sadece problem durumunda olanlar |
| `min_severity` | integer | ❌ | Min severity (0-5) |
| `limit` | integer | ❌ | Max sonuç (default: 100) |

**Severity Değerleri:**
| Değer | Anlam |
|-------|-------|
| 0 | Not classified |
| 1 | Information |
| 2 | Warning |
| 3 | Average |
| 4 | High |
| 5 | Disaster |

---

### get_trigger_details

Tek trigger detayı.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `trigger_id` | string | ✅ | Trigger ID |

---

### get_problems

Aktif problemler listesi.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `host_ids` | array | ❌ | Host ID listesi |
| `group_ids` | array | ❌ | Grup ID listesi |
| `severity_min` | integer | ❌ | Min severity (0-5) |
| `acknowledged` | boolean | ❌ | Sadece acknowledged olanlar |
| `suppressed` | boolean | ❌ | Suppressed durumu |
| `limit` | integer | ❌ | Max sonuç (default: 100) |

**Örnek Kullanım:**
```json
{
  "name": "get_problems",
  "arguments": {
    "severity_min": 3,
    "acknowledged": false,
    "limit": 50
  }
}
```

**Örnek Yanıt:**
```json
[
  {
    "eventid": "12345",
    "objectid": "67890",
    "name": "High CPU usage on {HOST.NAME}",
    "severity": "4",
    "clock": "1705312800",
    "acknowledged": "0",
    "hosts": [
      {"hostid": "10084", "name": "web-server-01"}
    ]
  }
]
```

---

### get_events

Event geçmişi sorgulama.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `host_ids` | array | ❌ | Host ID listesi |
| `trigger_ids` | array | ❌ | Trigger ID listesi |
| `time_from` | integer | ❌ | Başlangıç timestamp |
| `time_till` | integer | ❌ | Bitiş timestamp |
| `value` | integer | ❌ | 0=OK, 1=Problem |
| `limit` | integer | ❌ | Max sonuç |

---

### acknowledge_event ⚠️ WRITE

Problem acknowledge etme.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `event_ids` | array | ✅ | Event ID listesi |
| `message` | string | ❌ | Acknowledge mesajı |
| `action` | integer | ❌ | Acknowledge action (bkz. tablo) |

**Action Değerleri:**
| Değer | Anlam |
|-------|-------|
| 1 | Close problem |
| 2 | Acknowledge event |
| 4 | Add message |
| 8 | Change severity |
| 16 | Unacknowledge event |

**Tag:** `trigger`, `write`

---

## 📈 Item & History Tool'ları

### get_items

Item listesi sorgulama.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `host_ids` | array | ❌ | Host ID listesi |
| `group_ids` | array | ❌ | Grup ID listesi |
| `item_key` | string | ❌ | Item key pattern |
| `search_name` | string | ❌ | Item adında arama |
| `limit` | integer | ❌ | Max sonuç |

**Örnek Kullanım:**
```json
{
  "name": "get_items",
  "arguments": {
    "host_ids": ["10084"],
    "item_key": "system.cpu*",
    "limit": 20
  }
}
```

---

### get_item_details

Tek item detayı.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `item_id` | string | ✅ | Item ID |

---

### get_history

Item history verileri.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `item_ids` | array | ✅ | Item ID listesi |
| `history_type` | integer | ❌ | 0=float, 1=str, 2=log, 3=int, 4=text |
| `time_from` | integer | ❌ | Başlangıç timestamp |
| `time_till` | integer | ❌ | Bitiş timestamp |
| `limit` | integer | ❌ | Max sonuç (default: 100) |

**Örnek Kullanım:**
```json
{
  "name": "get_history",
  "arguments": {
    "item_ids": ["12345"],
    "time_from": 1705312800,
    "limit": 50
  }
}
```

**Örnek Yanıt:**
```json
[
  {
    "itemid": "12345",
    "clock": "1705312800",
    "value": "45.5",
    "ns": "123456789"
  }
]
```

---

### get_trends

Trend verileri (hourly/daily aggregated).

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `item_ids` | array | ✅ | Item ID listesi |
| `time_from` | integer | ❌ | Başlangıç timestamp |
| `time_till` | integer | ❌ | Bitiş timestamp |
| `limit` | integer | ❌ | Max sonuç |

**Örnek Yanıt:**
```json
[
  {
    "itemid": "12345",
    "clock": "1705312800",
    "num": "60",
    "value_min": "10.5",
    "value_avg": "45.2",
    "value_max": "89.3"
  }
]
```

---

## 🔧 Maintenance Tool'ları

### get_maintenances

Maintenance listesi.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `active_only` | boolean | ❌ | Sadece aktif olanlar |
| `host_ids` | array | ❌ | Bu host'ları etkileyen |

---

### create_maintenance ⚠️ WRITE

Yeni maintenance oluşturur.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `name` | string | ✅ | Maintenance adı |
| `host_ids` | array | ⚠️ | Host ID listesi |
| `group_ids` | array | ⚠️ | Grup ID listesi |
| `active_since` | integer | ✅ | Başlangıç timestamp |
| `active_till` | integer | ✅ | Bitiş timestamp |
| `description` | string | ❌ | Açıklama |
| `maintenance_type` | integer | ❌ | 0=data collection, 1=no data |

**Örnek Kullanım:**
```json
{
  "name": "create_maintenance",
  "arguments": {
    "name": "Web Server Maintenance",
    "host_ids": ["10084", "10085"],
    "active_since": 1705312800,
    "active_till": 1705320000,
    "description": "Scheduled patching"
  }
}
```

**Tag:** `maintenance`, `write`

---

### delete_maintenance ⚠️ WRITE

Maintenance siler.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `maintenance_ids` | array | ✅ | Maintenance ID listesi |

**Tag:** `maintenance`, `write`

---

## 📜 Script Tool'ları

### get_scripts

Script listesi.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `host_id` | string | ❌ | Bu host için kullanılabilir scriptler |

---

### execute_script ⚠️ WRITE

Host üzerinde script çalıştırır.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `script_id` | string | ✅ | Script ID |
| `host_id` | string | ✅ | Hedef host ID |

**Tag:** `script`, `write`

⚠️ **Dikkat:** Bu tool gerçek komut çalıştırır. Sadece güvenilir scriptler için kullanın.

---

## 📋 Template Tool'ları

### get_templates

Template listesi.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `filter_name` | string | ❌ | Template adı ile filtrele |
| `host_ids` | array | ❌ | Bu host'lara bağlı templateler |

---

## 👥 Kullanıcı Tool'ları

### get_users

Kullanıcı listesi.

**Parametreler:**
| Parametre | Tip | Zorunlu | Açıklama |
|-----------|-----|---------|----------|
| `usergroup_ids` | array | ❌ | Belirli grupların üyeleri |

---

### get_usergroups

Kullanıcı grupları listesi.

**Parametreler:** Yok

---

## 🚫 Read-Only Mode

`config.yaml`'da `mode.read_only: true` ayarlandığında, `write` tag'li tüm tool'lar gizlenir:

**Gizlenen tool'lar:**
- `host_enable`
- `host_disable`
- `acknowledge_event`
- `create_maintenance`
- `delete_maintenance`
- `execute_script`

Bu tool'lar `tools/list` yanıtında görünmez, Claude bunları kullanamaz.

---

## 🎯 Tag-Based Filtering

`disabled_tags` ile belirli kategorileri devre dışı bırakabilirsiniz:

```yaml
# ConfigMap
disabled_tags: ["script", "maintenance"]
```

Bu durumda script ve maintenance ile ilgili tüm tool'lar gizlenir.

---

## 💡 Kullanım İpuçları

### 1. Doğru Limit Kullanın
Büyük Zabbix ortamlarında her zaman `limit` parametresi kullanın:
```json
{"name": "get_hosts", "arguments": {"limit": 50}}
```

### 2. Filtreler Kombine Edin
```json
{
  "name": "get_problems",
  "arguments": {
    "group_ids": ["5"],
    "severity_min": 3,
    "acknowledged": false
  }
}
```

### 3. History için Zaman Aralığı
History sorguları için her zaman `time_from` ve `time_till` belirtin:
```json
{
  "name": "get_history",
  "arguments": {
    "item_ids": ["12345"],
    "time_from": 1705312800,
    "time_till": 1705320000
  }
}
```

### 4. Severity Filtreleme
Problem ve trigger sorgularında `severity_min` kullanarak gürültüyü azaltın:
- 3 (Average) ve üstü genellikle önemli
- 4 (High) ve 5 (Disaster) kritik

---

## 📊 Rate Limit Davranışı

Her tool çağrısı rate limit kontrolünden geçer:

1. **Cache Hit:** Rate limit sayılmaz
2. **Cache Miss:** Rate limit sayılır
3. **Rate Limit Aşıldı:** HTTP 429 döner

Rate limit hataları Claude'a iletilir, Claude kullanıcıya bilgi verir.
