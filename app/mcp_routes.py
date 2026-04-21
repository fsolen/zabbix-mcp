"""
MCP SSE Transport - OpenShift Compatible

Bu modül MCP protokolünü SSE üzerinden serve eder.
Claude Desktop veya diğer MCP client'lar bu endpoint'e bağlanabilir.
"""

import json
import asyncio
from typing import Any, AsyncGenerator
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
import httpx
import structlog

from .config_loader import load_config
from .cache import RedisCache

logger = structlog.get_logger()

router = APIRouter(prefix="/mcp", tags=["MCP"])

# Tool tanımları
TOOLS = [
    {
        "name": "get_zabbix_status",
        "description": "Zabbix MCP sisteminin genel durumunu gösterir. Redis bağlantısı ve versiyon bilgisi içerir.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_health",
        "description": "API sağlık durumunu kontrol eder.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "list_groups",
        "description": "Mevcut tüm Zabbix host gruplarını listeler.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_all_analysis",
        "description": "Tüm Zabbix host gruplarının analiz sonuçlarını getirir. Her grup için host sayısı, item sayısı, unsupported item oranı, trigger durumu ve noise score içerir.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_group_analysis",
        "description": "Belirli bir Zabbix host grubunun detaylı analizini getirir.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_name": {
                    "type": "string",
                    "description": "Analiz edilecek host grup adı (örn: 'Linux Servers')"
                }
            },
            "required": ["group_name"]
        }
    },
    {
        "name": "find_problematic_groups",
        "description": "Sorunlu host gruplarını bulur. Yüksek noise score veya çok sayıda unsupported item olan grupları listeler.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "noise_threshold": {
                    "type": "number",
                    "description": "Noise score eşiği (0-1 arası, default: 0.2)",
                    "default": 0.2
                },
                "unsupported_threshold": {
                    "type": "number",
                    "description": "Unsupported item yüzdesi eşiği (0-100, default: 10)",
                    "default": 10
                }
            },
            "required": []
        }
    },
    {
        "name": "get_summary",
        "description": "Tüm Zabbix altyapısının özet istatistiklerini getirir: toplam host, item, trigger sayıları ve genel sağlık durumu.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "search_groups",
        "description": "Host gruplarını anahtar kelimeye göre arar.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Aranacak anahtar kelime"
                }
            },
            "required": ["keyword"]
        }
    }
]


def get_cache() -> RedisCache:
    """Get or create cache instance"""
    cfg = load_config()
    return RedisCache(url=cfg["redis"]["url"], ttl=cfg["cache"]["ttl_sec"])


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
        groups = cache.get_all("analysis:*")
        
        total_hosts = 0
        total_items = 0
        total_unsupported = 0
        total_triggers = 0
        total_active = 0
        healthy_groups = 0
        
        for group in groups.values():
            metrics = group.get("metrics", {})
            recs = group.get("recommendations", [])
            
            total_hosts += metrics.get("hosts", 0)
            total_items += metrics.get("items", 0)
            total_unsupported += metrics.get("unsupported", 0)
            total_triggers += metrics.get("triggers", 0)
            total_active += metrics.get("active_triggers", 0)
            
            if "healthy" in recs:
                healthy_groups += 1
        
        unsupported_pct = (total_unsupported / total_items * 100) if total_items > 0 else 0
        health_pct = (healthy_groups / len(groups) * 100) if groups else 0
        
        return f"""📈 **Zabbix Altyapı Özeti**

**Genel İstatistikler:**
- Toplam Host Grubu: {len(groups)}
- Toplam Host: {total_hosts:,}
- Toplam Item: {total_items:,}
- Toplam Trigger: {total_triggers:,}

**Sağlık Durumu:**
- Sağlıklı Gruplar: {healthy_groups}/{len(groups)} (%{health_pct:.1f})
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


@router.get("/tools")
async def list_tools():
    """List available tools"""
    return {"tools": TOOLS}


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
                "result": {"tools": TOOLS}
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
    """SSE endpoint for MCP streaming"""
    
    async def event_generator() -> AsyncGenerator[str, None]:
        # Send initial connection event
        yield f"data: {json.dumps({'type': 'connected', 'server': 'zabbix-mcp'})}\n\n"
        
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
