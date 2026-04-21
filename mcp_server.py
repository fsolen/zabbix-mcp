#!/usr/bin/env python3
"""
Zabbix MCP Server - Claude Desktop Integration

Bu dosya Claude Desktop'un MCP protokolü ile Zabbix monitoring
verilerine erişmesini sağlar.

İki mod destekler:
1. LOCAL: Doğrudan OpenShift API'sine bağlanır
2. REMOTE: OpenShift'teki MCP endpoint'ini kullanır

Kurulum:
1. pip install mcp httpx
2. Claude Desktop config'e ekle (aşağıya bak)

Environment Variables:
- MCP_API_URL: Zabbix MCP API URL (default: OpenShift route)
- MCP_MODE: "local" veya "remote" (default: local)
"""

import asyncio
import json
import os
import httpx
from typing import Any
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    CallToolResult,
)

# Zabbix MCP API URL - OpenShift route
API_URL = os.getenv("MCP_API_URL", "https://zabbix-mcp.apps.ocptest.tmll.sahibindenlocal.net")
MCP_MODE = os.getenv("MCP_MODE", "local")  # local veya remote

server = Server("zabbix-mcp")


async def fetch_api(endpoint: str) -> dict:
    """Fetch data from Zabbix MCP API"""
    async with httpx.AsyncClient(verify=False, timeout=30) as client:
        response = await client.get(f"{API_URL}{endpoint}")
        response.raise_for_status()
        return response.json()


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools for Claude"""
    return [
        Tool(
            name="get_zabbix_status",
            description="Zabbix MCP sisteminin genel durumunu gösterir. Redis bağlantısı, versiyon bilgisi içerir.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_all_analysis",
            description="Tüm Zabbix host gruplarının analiz sonuçlarını getirir. Her grup için host sayısı, item sayısı, unsupported item oranı, trigger durumu ve noise score içerir.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_group_analysis",
            description="Belirli bir Zabbix host grubunun detaylı analizini getirir.",
            inputSchema={
                "type": "object",
                "properties": {
                    "group_name": {
                        "type": "string",
                        "description": "Analiz edilecek host grup adı (örn: 'Linux Servers', 'Windows Servers')"
                    }
                },
                "required": ["group_name"]
            }
        ),
        Tool(
            name="get_health",
            description="Zabbix MCP API'nin sağlık durumunu kontrol eder.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_metrics",
            description="Prometheus formatında sistem metriklerini getirir. API çağrı sayıları, cache durumu vb.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="find_problematic_groups",
            description="Sorunlu host gruplarını bulur. Yüksek noise score veya çok sayıda unsupported item olan grupları listeler.",
            inputSchema={
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
        ),
        Tool(
            name="get_summary",
            description="Tüm Zabbix altyapısının özet istatistiklerini getirir: toplam host, item, trigger sayıları ve genel sağlık durumu.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Execute tool calls from Claude"""
    
    try:
        if name == "get_zabbix_status":
            data = await fetch_api("/status")
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=f"Zabbix MCP Durumu:\n{json.dumps(data, indent=2, ensure_ascii=False)}"
                )]
            )
        
        elif name == "get_health":
            data = await fetch_api("/health")
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=f"Sağlık Durumu: {data.get('status', 'unknown')}"
                )]
            )
        
        elif name == "get_all_analysis":
            data = await fetch_api("/analysis")
            count = data.get("count", 0)
            groups = data.get("data", {})
            
            result = f"Toplam {count} grup analiz edildi:\n\n"
            for key, group in groups.items():
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
            
            return CallToolResult(
                content=[TextContent(type="text", text=result)]
            )
        
        elif name == "get_group_analysis":
            group_name = arguments.get("group_name", "")
            data = await fetch_api(f"/analysis/{group_name}")
            
            if "error" in data:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"Hata: {data['error']}")]
                )
            
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
            
            return CallToolResult(
                content=[TextContent(type="text", text=result)]
            )
        
        elif name == "get_metrics":
            async with httpx.AsyncClient(verify=False, timeout=30) as client:
                response = await client.get(f"{API_URL}/metrics")
                return CallToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"Prometheus Metrikleri:\n```\n{response.text[:2000]}...\n```"
                    )]
                )
        
        elif name == "find_problematic_groups":
            noise_threshold = arguments.get("noise_threshold", 0.2)
            unsupported_threshold = arguments.get("unsupported_threshold", 10)
            
            data = await fetch_api("/analysis")
            groups = data.get("data", {})
            
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
                        "metrics": metrics,
                        "noise": noise
                    })
            
            if not problematic:
                return CallToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"✅ Sorunlu grup bulunamadı! (noise > {noise_threshold}, unsupported > %{unsupported_threshold})"
                    )]
                )
            
            result = f"⚠️ {len(problematic)} sorunlu grup bulundu:\n\n"
            for p in sorted(problematic, key=lambda x: x["noise"], reverse=True):
                result += f"🔴 **{p['group']}**\n"
                result += f"   Sorunlar: {', '.join(p['issues'])}\n"
                result += f"   Hosts: {p['metrics'].get('hosts')}, Items: {p['metrics'].get('items')}\n\n"
            
            return CallToolResult(
                content=[TextContent(type="text", text=result)]
            )
        
        elif name == "get_summary":
            data = await fetch_api("/analysis")
            groups = data.get("data", {})
            
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
            
            result = f"""📈 **Zabbix Altyapı Özeti**

**Genel İstatistikler:**
- Toplam Host Grubu: {len(groups)}
- Toplam Host: {total_hosts:,}
- Toplam Item: {total_items:,}
- Toplam Trigger: {total_triggers:,}

**Sağlık Durumu:**
- Sağlıklı Gruplar: {healthy_groups}/{len(groups)} (%{health_pct:.1f})
- Unsupported Items: {total_unsupported:,} (%{unsupported_pct:.1f})
- Aktif Triggerlar: {total_active:,}

**Değerlendirme:**
{"✅ Altyapı genel olarak sağlıklı görünüyor." if health_pct > 80 else "⚠️ Bazı gruplar dikkat gerektiriyor."}
"""
            return CallToolResult(
                content=[TextContent(type="text", text=result)]
            )
        
        else:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Bilinmeyen tool: {name}")]
            )
    
    except httpx.HTTPError as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"API hatası: {str(e)}")]
        )
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Hata: {str(e)}")]
        )


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream)


if __name__ == "__main__":
    asyncio.run(main())
