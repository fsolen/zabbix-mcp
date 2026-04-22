"""
MCP SSE Transport - OpenShift Compatible

Bu modül MCP protokolünü SSE üzerinden serve eder.
Claude Desktop veya diğer MCP client'lar bu endpoint'e bağlanabilir.
"""

import json
import asyncio
import time
from typing import Any, AsyncGenerator
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
import httpx
import structlog

from .config_loader import load_config
from .cache import RedisCache
from .zabbix_client import ZabbixClient
from .rate_limiter import DistributedRateLimiter

logger = structlog.get_logger()

router = APIRouter(prefix="/mcp", tags=["MCP"])

# Global zabbix client (lazy init)
_zabbix_client = None
_client_lock = asyncio.Lock()

async def get_zabbix_client():
    """Get or create Zabbix client with lazy initialization"""
    global _zabbix_client
    async with _client_lock:
        if _zabbix_client is None:
            cfg = load_config()
            rate_limiter = DistributedRateLimiter(
                redis_url=cfg["redis"]["url"],
                calls_per_second=cfg.get("rate_limit", {}).get("calls_per_second", 5)
            )
            _zabbix_client = ZabbixClient(cfg["zabbix"], rate_limiter)
            await _zabbix_client.login()
            logger.info("zabbix_client_initialized")
    return _zabbix_client


# Tool tanımları - Query Tools (Read-Only)
TOOLS = [
    # === SYSTEM STATUS ===
    {
        "name": "get_zabbix_status",
        "description": "Zabbix MCP sisteminin genel durumunu gösterir.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "tags": ["system"]
    },
    {
        "name": "api_version",
        "description": "Zabbix API versiyonunu döndürür.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "tags": ["system"]
    },
    {
        "name": "get_summary",
        "description": "Tüm Zabbix altyapısının özet istatistiklerini getirir: toplam host, item, trigger sayıları ve genel sağlık durumu.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "tags": ["system"]
    },
    
    # === HOST MANAGEMENT ===
    {
        "name": "host_get",
        "description": "Host'ları listeler. Grup, template veya arama kriterlerine göre filtreleme yapılabilir.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "groupids": {"type": "array", "items": {"type": "string"}, "description": "Grup ID listesi"},
                "templateids": {"type": "array", "items": {"type": "string"}, "description": "Template ID listesi"},
                "search": {"type": "string", "description": "Host adında aranacak metin"},
                "limit": {"type": "integer", "default": 100, "description": "Maksimum sonuç sayısı"}
            },
            "required": []
        },
        "tags": ["host"]
    },
    {
        "name": "host_create",
        "description": "Yeni bir host oluşturur. (Yazma izni gerektirir)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Host adı"},
                "groupids": {"type": "array", "items": {"type": "string"}, "description": "Grup ID listesi"},
                "templateids": {"type": "array", "items": {"type": "string"}, "description": "Template ID listesi"},
                "ip": {"type": "string", "description": "Host IP adresi"},
                "description": {"type": "string", "description": "Host açıklaması"}
            },
            "required": ["name", "groupids"]
        },
        "tags": ["host", "write"]
    },
    {
        "name": "host_delete",
        "description": "Host'ları siler. (Yazma izni gerektirir)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hostids": {"type": "array", "items": {"type": "string"}, "description": "Silinecek host ID listesi"}
            },
            "required": ["hostids"]
        },
        "tags": ["host", "write"]
    },
    
    # === HOST GROUP MANAGEMENT ===
    {
        "name": "hostgroup_get",
        "description": "Host gruplarını listeler.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Grup adında aranacak metin"},
                "limit": {"type": "integer", "default": 500}
            },
            "required": []
        },
        "tags": ["hostgroup"]
    },
    {
        "name": "hostgroup_create",
        "description": "Yeni host grubu oluşturur. (Yazma izni gerektirir)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Grup adı"}
            },
            "required": ["name"]
        },
        "tags": ["hostgroup", "write"]
    },
    
    # === TEMPLATE MANAGEMENT ===
    {
        "name": "template_get",
        "description": "Template'leri listeler.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Template adında aranacak metin"},
                "hostids": {"type": "array", "items": {"type": "string"}, "description": "Host ID listesi"},
                "limit": {"type": "integer", "default": 200}
            },
            "required": []
        },
        "tags": ["template"]
    },
    
    # === ITEM MANAGEMENT ===
    {
        "name": "item_get",
        "description": "Item'ları listeler. Host, grup veya template'e göre filtreleme yapılabilir.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hostids": {"type": "array", "items": {"type": "string"}, "description": "Host ID listesi"},
                "groupids": {"type": "array", "items": {"type": "string"}, "description": "Grup ID listesi"},
                "search": {"type": "string", "description": "Item adında aranacak metin"},
                "filter": {"type": "object", "description": "Filtre (örn: {\"state\": 1} unsupported için)"},
                "limit": {"type": "integer", "default": 500}
            },
            "required": []
        },
        "tags": ["item"]
    },
    
    # === TRIGGER MANAGEMENT ===
    {
        "name": "trigger_get",
        "description": "Trigger'ları listeler. Severity ve durum filtrelemesi yapılabilir.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hostids": {"type": "array", "items": {"type": "string"}},
                "groupids": {"type": "array", "items": {"type": "string"}},
                "only_problems": {"type": "boolean", "default": False, "description": "Sadece problem olan trigger'lar"},
                "min_severity": {"type": "integer", "description": "Minimum severity (0-5)"},
                "limit": {"type": "integer", "default": 500}
            },
            "required": []
        },
        "tags": ["trigger"]
    },
    
    # === PROBLEM & EVENT MANAGEMENT ===
    {
        "name": "problem_get",
        "description": "Aktif problemleri listeler. Severity ve zaman filtrelemesi yapılabilir.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hostids": {"type": "array", "items": {"type": "string"}},
                "groupids": {"type": "array", "items": {"type": "string"}},
                "min_severity": {"type": "integer", "description": "Minimum severity (0-5)"},
                "acknowledged": {"type": "boolean", "description": "Sadece acknowledge edilmiş/edilmemiş"},
                "limit": {"type": "integer", "default": 500}
            },
            "required": []
        },
        "tags": ["problem"]
    },
    {
        "name": "event_get",
        "description": "Event'leri listeler.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hostids": {"type": "array", "items": {"type": "string"}},
                "time_from": {"type": "integer", "description": "Unix timestamp - başlangıç"},
                "time_till": {"type": "integer", "description": "Unix timestamp - bitiş"},
                "limit": {"type": "integer", "default": 500}
            },
            "required": []
        },
        "tags": ["event"]
    },
    {
        "name": "event_acknowledge",
        "description": "Event'leri acknowledge eder veya kapatır. (Yazma izni gerektirir)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "eventids": {"type": "array", "items": {"type": "string"}, "description": "Event ID listesi"},
                "message": {"type": "string", "description": "Acknowledge mesajı"},
                "action": {"type": "integer", "default": 1, "description": "1=close, 2=ack, 4=add message"}
            },
            "required": ["eventids"]
        },
        "tags": ["event", "write"]
    },
    
    # === HISTORY & TRENDS ===
    {
        "name": "history_get",
        "description": "Item'ların geçmiş verilerini getirir.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemids": {"type": "array", "items": {"type": "string"}, "description": "Item ID listesi"},
                "history_type": {"type": "integer", "default": 0, "description": "0=float, 1=string, 2=log, 3=int, 4=text"},
                "time_from": {"type": "integer", "description": "Unix timestamp - başlangıç"},
                "time_till": {"type": "integer", "description": "Unix timestamp - bitiş"},
                "limit": {"type": "integer", "default": 1000}
            },
            "required": ["itemids"]
        },
        "tags": ["history"]
    },
    {
        "name": "trend_get",
        "description": "Item'ların trend verilerini getirir (saatlik min/max/avg).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemids": {"type": "array", "items": {"type": "string"}, "description": "Item ID listesi"},
                "time_from": {"type": "integer"},
                "time_till": {"type": "integer"},
                "limit": {"type": "integer", "default": 500}
            },
            "required": ["itemids"]
        },
        "tags": ["history"]
    },
    
    # === MAINTENANCE ===
    {
        "name": "maintenance_get",
        "description": "Maintenance periyotlarını listeler.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hostids": {"type": "array", "items": {"type": "string"}},
                "groupids": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 100}
            },
            "required": []
        },
        "tags": ["maintenance"]
    },
    {
        "name": "maintenance_create",
        "description": "Yeni maintenance periyodu oluşturur. (Yazma izni gerektirir)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Maintenance adı"},
                "active_since": {"type": "integer", "description": "Başlangıç Unix timestamp"},
                "active_till": {"type": "integer", "description": "Bitiş Unix timestamp"},
                "hostids": {"type": "array", "items": {"type": "string"}},
                "groupids": {"type": "array", "items": {"type": "string"}},
                "description": {"type": "string"}
            },
            "required": ["name", "active_since", "active_till"]
        },
        "tags": ["maintenance", "write"]
    },
    
    # === USER & PROXY ===
    {
        "name": "user_get",
        "description": "Kullanıcıları listeler.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string"},
                "limit": {"type": "integer", "default": 100}
            },
            "required": []
        },
        "tags": ["user"]
    },
    {
        "name": "proxy_get",
        "description": "Proxy'leri listeler.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 100}},
            "required": []
        },
        "tags": ["proxy"]
    },
    
    # === SCRIPTS ===
    {
        "name": "script_get",
        "description": "Script'leri listeler.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 100}},
            "required": []
        },
        "tags": ["script"]
    },
    {
        "name": "script_execute",
        "description": "Bir host üzerinde script çalıştırır. (Yazma izni gerektirir)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scriptid": {"type": "string", "description": "Script ID"},
                "hostid": {"type": "string", "description": "Host ID"}
            },
            "required": ["scriptid", "hostid"]
        },
        "tags": ["script", "write"]
    },
    
    # === OTHER QUERIES ===
    {
        "name": "graph_get",
        "description": "Graph'ları listeler.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hostids": {"type": "array", "items": {"type": "string"}},
                "search": {"type": "string"},
                "limit": {"type": "integer", "default": 200}
            },
            "required": []
        },
        "tags": ["graph"]
    },
    {
        "name": "action_get",
        "description": "Action'ları listeler.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "eventsource": {"type": "integer", "description": "0=trigger, 1=discovery, 2=autoregistration"},
                "limit": {"type": "integer", "default": 100}
            },
            "required": []
        },
        "tags": ["action"]
    },
    {
        "name": "mediatype_get",
        "description": "Media type'ları listeler.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 100}},
            "required": []
        },
        "tags": ["mediatype"]
    },
    {
        "name": "sla_get",
        "description": "SLA'ları listeler.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 100}},
            "required": []
        },
        "tags": ["sla"]
    },
    {
        "name": "service_get",
        "description": "Service'leri listeler.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 200}},
            "required": []
        },
        "tags": ["service"]
    },
    {
        "name": "usermacro_get",
        "description": "User macro'ları listeler.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hostids": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 500}
            },
            "required": []
        },
        "tags": ["macro"]
    },
    
    # === CACHED ANALYSIS (from worker) ===
    {
        "name": "get_all_analysis",
        "description": "Cache'deki tüm grup analizlerini getirir (worker tarafından periyodik güncellenir).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "tags": ["analysis"]
    },
    {
        "name": "get_group_analysis",
        "description": "Cache'deki belirli bir grubun analizini getirir.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_name": {"type": "string", "description": "Grup adı"}
            },
            "required": ["group_name"]
        },
        "tags": ["analysis"]
    },
    {
        "name": "find_problematic_groups",
        "description": "Cache'deki sorunlu grupları bulur.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "noise_threshold": {"type": "number", "default": 0.2},
                "unsupported_threshold": {"type": "number", "default": 10}
            },
            "required": []
        },
        "tags": ["analysis"]
    },
    {
        "name": "search_groups",
        "description": "Cache'de grup arar.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "Aranacak kelime"}
            },
            "required": ["keyword"]
        },
        "tags": ["analysis"]
    }
]


def get_cache() -> RedisCache:
    """Get or create cache instance"""
    cfg = load_config()
    return RedisCache(url=cfg["redis"]["url"], ttl=cfg["cache"]["ttl_sec"])


def is_write_operation(tool_name: str) -> bool:
    """Check if tool is a write operation"""
    for tool in TOOLS:
        if tool["name"] == tool_name:
            return "write" in tool.get("tags", [])
    return False


async def execute_tool(name: str, arguments: dict, cache: RedisCache) -> str:
    """Execute a tool and return result"""
    
    if name == "get_zabbix_status":
        health = cache.health_check()
        return json.dumps({
            "status": "healthy" if health["redis_connected"] else "degraded",
            "cache": health,
            "version": "1.0.0"
        }, indent=2)
    
    elif name == "get_health":
        health = cache.health_check()
        status = "✅ Sağlıklı" if health["redis_connected"] else "⚠️ Redis bağlantısı yok"
        return f"Sistem Durumu: {status}\nRedis: {'Bağlı' if health['redis_connected'] else 'Bağlı Değil'}"
    
    elif name == "list_groups":
        groups = cache.get_all("analysis:*")
        if not groups:
            return "Henüz analiz verisi yok. Worker podları çalışıyor mu?"
        
        result = f"📋 **{len(groups)} Host Grubu Mevcut:**\n\n"
        for key, group in sorted(groups.items()):
            metrics = group.get("metrics", {})
            result += f"• {group.get('group', key)} ({metrics.get('hosts', 0)} host, {metrics.get('items', 0)} item)\n"
        
        return result
    
    elif name == "get_all_analysis":
        data = cache.get_all("analysis:*")
        result = f"Toplam {len(data)} grup analiz edildi:\n\n"
        
        for key, group in data.items():
            metrics = group.get("metrics", {})
            analysis = group.get("analysis", {})
            recs = group.get("recommendations", [])
            
            result += f"📊 **{group.get('group', key)}**\n"
            result += f"   - Hosts: {metrics.get('hosts', 0)}\n"
            result += f"   - Items: {metrics.get('items', 0)}\n"
            result += f"   - Unsupported: {metrics.get('unsupported', 0)}\n"
            result += f"   - Active Triggers: {metrics.get('active_triggers', 0)}/{metrics.get('triggers', 0)}\n"
            result += f"   - Noise Score: {analysis.get('noise_score', 0)}\n"
            result += f"   - Öneri: {', '.join(recs)}\n\n"
        
        return result
    
    elif name == "get_group_analysis":
        group_name = arguments.get("group_name", "")
        data = cache.get(f"analysis:{group_name}")
        
        if not data:
            return f"Grup bulunamadı: {group_name}"
        
        metrics = data.get("metrics", {})
        analysis = data.get("analysis", {})
        recs = data.get("recommendations", [])
        
        result = f"📊 **{data.get('group', group_name)}** Detaylı Analiz:\n\n"
        result += f"**Metrikler:**\n"
        result += f"- Host Sayısı: {metrics.get('hosts', 0)}\n"
        result += f"- Item Sayısı: {metrics.get('items', 0)}\n"
        result += f"- Unsupported Items: {metrics.get('unsupported', 0)}\n"
        result += f"- Trigger Sayısı: {metrics.get('triggers', 0)}\n"
        result += f"- Aktif Triggerlar: {metrics.get('active_triggers', 0)}\n\n"
        result += f"**Analiz:**\n"
        result += f"- Noise Score: {analysis.get('noise_score', 0)}\n\n"
        result += f"**Öneriler:** {', '.join(recs)}"
        
        return result
    
    elif name == "find_problematic_groups":
        noise_threshold = arguments.get("noise_threshold", 0.2)
        unsupported_threshold = arguments.get("unsupported_threshold", 10)
        
        groups = cache.get_all("analysis:*")
        problematic = []
        
        for key, group in groups.items():
            metrics = group.get("metrics", {})
            analysis = group.get("analysis", {})
            
            items = metrics.get("items", 0)
            unsupported = metrics.get("unsupported", 0)
            noise = analysis.get("noise_score", 0)
            
            unsupported_pct = (unsupported / items * 100) if items > 0 else 0
            
            issues = []
            if noise > noise_threshold:
                issues.append(f"Yüksek noise ({noise:.2f})")
            if unsupported_pct > unsupported_threshold:
                issues.append(f"Çok unsupported item (%{unsupported_pct:.1f})")
            
            if issues:
                problematic.append({
                    "group": group.get("group", key),
                    "issues": issues,
                    "noise": noise
                })
        
        if not problematic:
            return f"✅ Sorunlu grup bulunamadı!"
        
        result = f"⚠️ {len(problematic)} sorunlu grup bulundu:\n\n"
        for p in sorted(problematic, key=lambda x: x["noise"], reverse=True):
            result += f"🔴 **{p['group']}**\n"
            result += f"   Sorunlar: {', '.join(p['issues'])}\n\n"
        
        return result
    
    elif name == "get_summary":
        # Get global stats (unique counts from Zabbix)
        global_stats = cache.get("global:stats")
        groups = cache.get_all("analysis:*")
        
        if global_stats:
            # Use accurate global stats
            total_hosts = global_stats.get("hosts", 0)
            total_items = global_stats.get("items", 0)
            total_unsupported = global_stats.get("unsupported", 0)
            total_triggers = global_stats.get("triggers", 0)
            total_active = global_stats.get("active_triggers", 0)
            total_groups = global_stats.get("groups", 0)
        else:
            # Fallback to group-based calculation (may have duplicates)
            total_hosts = 0
            total_items = 0
            total_unsupported = 0
            total_triggers = 0
            total_active = 0
            total_groups = len(groups)
            
            for group in groups.values():
                metrics = group.get("metrics", {})
                total_hosts += metrics.get("hosts", 0)
                total_items += metrics.get("items", 0)
                total_unsupported += metrics.get("unsupported", 0)
                total_triggers += metrics.get("triggers", 0)
                total_active += metrics.get("active_triggers", 0)
        
        # Count healthy groups from analyzed data
        healthy_groups = 0
        groups_with_hosts = 0
        for group in groups.values():
            recs = group.get("recommendations", [])
            hosts = group.get("metrics", {}).get("hosts", 0)
            if hosts > 0:
                groups_with_hosts += 1
            if "healthy" in recs:
                healthy_groups += 1
        
        unsupported_pct = (total_unsupported / total_items * 100) if total_items > 0 else 0
        health_pct = (healthy_groups / groups_with_hosts * 100) if groups_with_hosts > 0 else 0
        
        return f"""📈 **Zabbix Altyapı Özeti**

**Genel İstatistikler:**
- Toplam Host Grubu: {total_groups} ({groups_with_hosts} aktif)
- Toplam Host: {total_hosts:,}
- Toplam Item: {total_items:,}
- Toplam Trigger: {total_triggers:,}

**Sağlık Durumu:**
- Sağlıklı Gruplar: {healthy_groups}/{groups_with_hosts} (%{health_pct:.1f})
- Unsupported Items: {total_unsupported:,} (%{unsupported_pct:.1f})
- Aktif Triggerlar: {total_active:,}
"""
    
    elif name == "search_groups":
        keyword = arguments.get("keyword", "").lower()
        if not keyword:
            return "Anahtar kelime gerekli."
        
        groups = cache.get_all("analysis:*")
        matches = []
        
        for key, group in groups.items():
            group_name = group.get("group", key)
            if keyword in group_name.lower():
                metrics = group.get("metrics", {})
                matches.append({
                    "name": group_name,
                    "hosts": metrics.get("hosts", 0),
                    "items": metrics.get("items", 0)
                })
        
        if not matches:
            return f"'{keyword}' ile eşleşen grup bulunamadı."
        
        result = f"🔍 **'{keyword}' için {len(matches)} sonuç:**\n\n"
        for m in matches:
            result += f"• {m['name']} ({m['hosts']} host, {m['items']} item)\n"
        
        return result
    
    # ==================== DIRECT ZABBIX API CALLS ====================
    # These tools query Zabbix directly (not from cache)
    
    cfg = load_config()
    read_only = cfg.get("mode", {}).get("read_only", True)
    
    # Check if write operation in read-only mode
    if read_only and is_write_operation(name):
        return f"❌ Hata: '{name}' yazma işlemi gerektirir ancak sistem read-only modda."
    
    try:
        client = await get_zabbix_client()
        
        # === API INFO ===
        if name == "api_version":
            version = await client.api_version()
            return f"Zabbix API Versiyonu: {version}"
        
        # === HOST ===
        elif name == "host_get":
            hosts = await client.host_get(
                groupids=arguments.get("groupids"),
                templateids=arguments.get("templateids"),
                search=arguments.get("search"),
                limit=arguments.get("limit", 100)
            )
            if not hosts:
                return "Hiç host bulunamadı."
            
            result = f"📋 **{len(hosts)} Host:**\n\n"
            for h in hosts[:50]:  # Limit display
                status = "✅" if h.get("status") == "0" else "⛔"
                groups = ", ".join([g["name"] for g in h.get("groups", [])])
                result += f"{status} **{h['name']}** (ID: {h['hostid']})\n"
                result += f"   Gruplar: {groups}\n"
            if len(hosts) > 50:
                result += f"\n... ve {len(hosts) - 50} host daha"
            return result
        
        elif name == "host_create":
            interfaces = None
            if arguments.get("ip"):
                interfaces = [{
                    "type": 1, "main": 1, "useip": 1, 
                    "ip": arguments["ip"], "dns": "", "port": "10050"
                }]
            result = await client.host_create(
                name=arguments["name"],
                groupids=arguments["groupids"],
                interfaces=interfaces,
                templateids=arguments.get("templateids"),
                description=arguments.get("description")
            )
            return f"✅ Host oluşturuldu: {arguments['name']} (ID: {result.get('hostids', ['?'])[0]})"
        
        elif name == "host_delete":
            result = await client.host_delete(arguments["hostids"])
            return f"✅ {len(arguments['hostids'])} host silindi."
        
        # === HOST GROUP ===
        elif name == "hostgroup_get":
            groups = await client.hostgroup_get(
                search=arguments.get("search"),
                limit=arguments.get("limit", 500)
            )
            if not groups:
                return "Hiç grup bulunamadı."
            
            result = f"📋 **{len(groups)} Host Grubu:**\n\n"
            for g in groups:
                host_count = g.get("hosts", 0)
                result += f"• **{g['name']}** (ID: {g['groupid']}, {host_count} host)\n"
            return result
        
        elif name == "hostgroup_create":
            result = await client.hostgroup_create(arguments["name"])
            return f"✅ Grup oluşturuldu: {arguments['name']} (ID: {result.get('groupids', ['?'])[0]})"
        
        # === TEMPLATE ===
        elif name == "template_get":
            templates = await client.template_get(
                search=arguments.get("search"),
                hostids=arguments.get("hostids"),
                limit=arguments.get("limit", 200)
            )
            if not templates:
                return "Hiç template bulunamadı."
            
            result = f"📋 **{len(templates)} Template:**\n\n"
            for t in templates[:30]:
                items = t.get("items", 0)
                triggers = t.get("triggers", 0)
                result += f"• **{t['name']}** (ID: {t['templateid']})\n"
                result += f"   Items: {items}, Triggers: {triggers}\n"
            return result
        
        # === ITEM ===
        elif name == "item_get":
            items = await client.item_get(
                hostids=arguments.get("hostids"),
                groupids=arguments.get("groupids"),
                search=arguments.get("search"),
                filter_dict=arguments.get("filter"),
                limit=arguments.get("limit", 500)
            )
            if not items:
                return "Hiç item bulunamadı."
            
            result = f"📋 **{len(items)} Item:**\n\n"
            for i in items[:30]:
                state = "⚠️" if i.get("state") == "1" else "✅"
                host = i.get("hosts", [{}])[0].get("name", "?")
                result += f"{state} **{i['name']}** ({host})\n"
                result += f"   Key: {i['key_']}, Son değer: {i.get('lastvalue', 'N/A')}\n"
            if len(items) > 30:
                result += f"\n... ve {len(items) - 30} item daha"
            return result
        
        # === TRIGGER ===
        elif name == "trigger_get":
            triggers = await client.trigger_get(
                hostids=arguments.get("hostids"),
                groupids=arguments.get("groupids"),
                only_problems=arguments.get("only_problems", False),
                min_severity=arguments.get("min_severity"),
                limit=arguments.get("limit", 500)
            )
            if not triggers:
                return "Hiç trigger bulunamadı."
            
            severity_icons = ["⚪", "🔵", "🟡", "🟠", "🔴", "🟣"]
            result = f"📋 **{len(triggers)} Trigger:**\n\n"
            for t in triggers[:30]:
                sev = int(t.get("priority", 0))
                icon = severity_icons[sev] if sev < 6 else "⚪"
                status = "🔥" if t.get("value") == "1" else "✅"
                host = t.get("hosts", [{}])[0].get("name", "?")
                result += f"{status}{icon} **{t['description']}** ({host})\n"
            return result
        
        # === PROBLEM ===
        elif name == "problem_get":
            problems = await client.problem_get(
                hostids=arguments.get("hostids"),
                groupids=arguments.get("groupids"),
                min_severity=arguments.get("min_severity"),
                acknowledged=arguments.get("acknowledged"),
                limit=arguments.get("limit", 500)
            )
            if not problems:
                return "✅ Aktif problem yok!"
            
            severity_names = ["Not classified", "Information", "Warning", "Average", "High", "Disaster"]
            result = f"🚨 **{len(problems)} Aktif Problem:**\n\n"
            for p in problems[:30]:
                sev = int(p.get("severity", 0))
                sev_name = severity_names[sev] if sev < 6 else "Unknown"
                ack = "✅" if p.get("acknowledged") == "1" else "❌"
                host = p.get("hosts", [{}])[0].get("name", "?")
                result += f"🔴 **{p['name']}**\n"
                result += f"   Host: {host}, Severity: {sev_name}, Ack: {ack}\n"
            return result
        
        # === EVENT ===
        elif name == "event_get":
            events = await client.event_get(
                hostids=arguments.get("hostids"),
                time_from=arguments.get("time_from"),
                time_till=arguments.get("time_till"),
                limit=arguments.get("limit", 500)
            )
            if not events:
                return "Hiç event bulunamadı."
            
            result = f"📋 **{len(events)} Event:**\n\n"
            for e in events[:20]:
                host = e.get("hosts", [{}])[0].get("name", "?")
                ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(e.get("clock", 0))))
                result += f"• [{ts}] **{e.get('name', 'N/A')}** ({host})\n"
            return result
        
        elif name == "event_acknowledge":
            result = await client.event_acknowledge(
                eventids=arguments["eventids"],
                message=arguments.get("message"),
                action=arguments.get("action", 1)
            )
            return f"✅ {len(arguments['eventids'])} event acknowledge edildi."
        
        # === HISTORY & TREND ===
        elif name == "history_get":
            history = await client.history_get(
                itemids=arguments["itemids"],
                history_type=arguments.get("history_type", 0),
                time_from=arguments.get("time_from"),
                time_till=arguments.get("time_till"),
                limit=arguments.get("limit", 1000)
            )
            if not history:
                return "Hiç history verisi bulunamadı."
            
            result = f"📊 **{len(history)} History Kaydı:**\n\n"
            for h in history[:20]:
                ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(h.get("clock", 0))))
                result += f"• [{ts}] {h.get('value', 'N/A')}\n"
            return result
        
        elif name == "trend_get":
            trends = await client.trend_get(
                itemids=arguments["itemids"],
                time_from=arguments.get("time_from"),
                time_till=arguments.get("time_till"),
                limit=arguments.get("limit", 500)
            )
            if not trends:
                return "Hiç trend verisi bulunamadı."
            
            result = f"📊 **{len(trends)} Trend Kaydı:**\n\n"
            for t in trends[:20]:
                ts = time.strftime("%Y-%m-%d %H:00", time.localtime(int(t.get("clock", 0))))
                result += f"• [{ts}] min={t.get('value_min')}, avg={t.get('value_avg')}, max={t.get('value_max')}\n"
            return result
        
        # === MAINTENANCE ===
        elif name == "maintenance_get":
            maintenances = await client.maintenance_get(
                hostids=arguments.get("hostids"),
                groupids=arguments.get("groupids"),
                limit=arguments.get("limit", 100)
            )
            if not maintenances:
                return "Hiç maintenance bulunamadı."
            
            result = f"🔧 **{len(maintenances)} Maintenance:**\n\n"
            for m in maintenances:
                start = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(m.get("active_since", 0))))
                end = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(m.get("active_till", 0))))
                result += f"• **{m['name']}**\n"
                result += f"   {start} - {end}\n"
            return result
        
        elif name == "maintenance_create":
            result = await client.maintenance_create(
                name=arguments["name"],
                active_since=arguments["active_since"],
                active_till=arguments["active_till"],
                hostids=arguments.get("hostids"),
                groupids=arguments.get("groupids"),
                description=arguments.get("description")
            )
            return f"✅ Maintenance oluşturuldu: {arguments['name']}"
        
        # === USER ===
        elif name == "user_get":
            users = await client.user_get(
                search=arguments.get("search"),
                limit=arguments.get("limit", 100)
            )
            if not users:
                return "Hiç kullanıcı bulunamadı."
            
            result = f"👤 **{len(users)} Kullanıcı:**\n\n"
            for u in users:
                role = u.get("role", {}).get("name", "N/A")
                result += f"• **{u['username']}** ({u.get('name', '')} {u.get('surname', '')}) - {role}\n"
            return result
        
        # === PROXY ===
        elif name == "proxy_get":
            proxies = await client.proxy_get(limit=arguments.get("limit", 100))
            if not proxies:
                return "Hiç proxy bulunamadı."
            
            result = f"🖥️ **{len(proxies)} Proxy:**\n\n"
            for p in proxies:
                hosts = p.get("hosts", 0)
                mode = "Active" if p.get("operating_mode") == "0" else "Passive"
                result += f"• **{p['name']}** ({mode}, {hosts} host)\n"
            return result
        
        # === SCRIPT ===
        elif name == "script_get":
            scripts = await client.script_get(limit=arguments.get("limit", 100))
            if not scripts:
                return "Hiç script bulunamadı."
            
            result = f"📜 **{len(scripts)} Script:**\n\n"
            for s in scripts:
                result += f"• **{s['name']}** (ID: {s['scriptid']})\n"
            return result
        
        elif name == "script_execute":
            result = await client.script_execute(
                scriptid=arguments["scriptid"],
                hostid=arguments["hostid"]
            )
            return f"✅ Script çalıştırıldı:\n{result.get('value', 'Output yok')}"
        
        # === OTHER ===
        elif name == "graph_get":
            graphs = await client.graph_get(
                hostids=arguments.get("hostids"),
                search=arguments.get("search"),
                limit=arguments.get("limit", 200)
            )
            if not graphs:
                return "Hiç graph bulunamadı."
            
            result = f"📈 **{len(graphs)} Graph:**\n\n"
            for g in graphs[:30]:
                host = g.get("hosts", [{}])[0].get("name", "?")
                result += f"• **{g['name']}** ({host})\n"
            return result
        
        elif name == "action_get":
            actions = await client.action_get(
                eventsource=arguments.get("eventsource"),
                limit=arguments.get("limit", 100)
            )
            if not actions:
                return "Hiç action bulunamadı."
            
            result = f"⚡ **{len(actions)} Action:**\n\n"
            for a in actions:
                status = "✅" if a.get("status") == "0" else "⛔"
                result += f"{status} **{a['name']}**\n"
            return result
        
        elif name == "mediatype_get":
            mediatypes = await client.mediatype_get(limit=arguments.get("limit", 100))
            if not mediatypes:
                return "Hiç media type bulunamadı."
            
            result = f"📧 **{len(mediatypes)} Media Type:**\n\n"
            for m in mediatypes:
                status = "✅" if m.get("status") == "0" else "⛔"
                result += f"{status} **{m['name']}**\n"
            return result
        
        elif name == "sla_get":
            slas = await client.sla_get(limit=arguments.get("limit", 100))
            if not slas:
                return "Hiç SLA bulunamadı."
            
            result = f"📊 **{len(slas)} SLA:**\n\n"
            for s in slas:
                result += f"• **{s.get('name', 'N/A')}**\n"
            return result
        
        elif name == "service_get":
            services = await client.service_get(limit=arguments.get("limit", 200))
            if not services:
                return "Hiç service bulunamadı."
            
            result = f"🔧 **{len(services)} Service:**\n\n"
            for s in services:
                result += f"• **{s['name']}** (Status: {s.get('status', 'N/A')})\n"
            return result
        
        elif name == "usermacro_get":
            macros = await client.usermacro_get(
                hostids=arguments.get("hostids"),
                limit=arguments.get("limit", 500)
            )
            if not macros:
                return "Hiç macro bulunamadı."
            
            result = f"🔤 **{len(macros)} User Macro:**\n\n"
            for m in macros[:30]:
                host = m.get("hosts", [{}])[0].get("name", "Global") if m.get("hosts") else "Global"
                result += f"• **{m.get('macro', 'N/A')}** = {m.get('value', 'N/A')} ({host})\n"
            return result
        
    except Exception as e:
        logger.error("tool_execution_error", tool=name, error=str(e))
        return f"❌ Hata: {str(e)}"
    
    return f"Bilinmeyen tool: {name}"


# MCP JSON-RPC Endpoints

@router.get("/")
async def mcp_info():
    """MCP server bilgisi"""
    return {
        "name": "zabbix-mcp",
        "version": "1.0.0",
        "description": "Zabbix Monitoring Control Plane - MCP Server",
        "transport": "sse",
        "endpoints": {
            "sse": "/mcp/sse",
            "message": "/mcp/message"
        }
    }


def get_filtered_tools():
    """Get tools filtered by disabled_tags and read_only mode"""
    cfg = load_config()
    disabled_tags = set(cfg.get("disabled_tags", []))
    read_only = cfg.get("mode", {}).get("read_only", True)
    
    # Add 'write' to disabled tags if read_only mode
    if read_only:
        disabled_tags.add("write")
    
    filtered = []
    for tool in TOOLS:
        tool_tags = set(tool.get("tags", []))
        # Skip if any of tool's tags is in disabled_tags
        if not tool_tags.intersection(disabled_tags):
            # Remove tags from output (not part of MCP spec)
            clean_tool = {k: v for k, v in tool.items() if k != "tags"}
            filtered.append(clean_tool)
    
    return filtered


@router.get("/tools")
async def list_tools():
    """List available tools"""
    return {"tools": get_filtered_tools()}


@router.post("/message")
async def handle_message(request: Request):
    """Handle MCP JSON-RPC messages"""
    try:
        body = await request.json()
        method = body.get("method", "")
        params = body.get("params", {})
        msg_id = body.get("id")
        
        cache = get_cache()
        
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {
                        "name": "zabbix-mcp",
                        "version": "1.0.0"
                    },
                    "capabilities": {
                        "tools": {}
                    }
                }
            }
        
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": get_filtered_tools()}
            }
        
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            
            result = await execute_tool(tool_name, arguments, cache)
            
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {"type": "text", "text": result}
                    ]
                }
            }
        
        else:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }
    
    except Exception as e:
        logger.error("mcp_message_error", error=str(e))
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32603, "message": str(e)}
        }, status_code=500)


@router.get("/sse")
async def sse_endpoint(request: Request):
    """SSE endpoint for MCP streaming - GET for receiving events"""
    
    async def event_generator() -> AsyncGenerator[str, None]:
        # Send initial connection event with endpoint info
        yield f"data: {json.dumps({'type': 'endpoint', 'url': '/mcp/sse'})}\n\n"
        
        # Keep connection alive
        while True:
            if await request.is_disconnected():
                break
            
            # Send heartbeat every 30 seconds
            yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
            await asyncio.sleep(30)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post("/sse")
async def sse_message(request: Request):
    """SSE endpoint for MCP streaming - POST for sending messages"""
    try:
        body = await request.json()
        method = body.get("method", "")
        params = body.get("params", {})
        msg_id = body.get("id")
        
        cache = get_cache()
        
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {
                        "name": "zabbix-mcp",
                        "version": "1.0.0"
                    },
                    "capabilities": {
                        "tools": {}
                    }
                }
            }
        
        elif method == "notifications/initialized":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": get_filtered_tools()}
            }
        
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            
            result = await execute_tool(tool_name, arguments, cache)
            
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {"type": "text", "text": result}
                    ]
                }
            }
        
        else:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }
    
    except Exception as e:
        logger.error("sse_post_error", error=str(e))
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32603, "message": str(e)}
        }, status_code=500)
