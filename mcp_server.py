#!/usr/bin/env python3
"""
Zabbix MCP Proxy - Claude Desktop Bridge

Bu script sadece Claude Desktop ile OpenShift MCP arasında köprü görevi görür.
Tüm iş mantığı OpenShift'te çalışır, bu proxy sadece HTTP call yapar.

Kurulum:
    pip install mcp httpx

Claude Desktop Config (~/.config/claude/claude_desktop_config.json):
{
  "mcpServers": {
    "zabbix": {
      "command": "python",
      "args": ["/path/to/mcp_server.py"],
      "env": {
        "MCP_API_URL": "https://zabbix-mcp.apps.ocptest.tmll.sahibindenlocal.net"
      }
    }
  }
}
"""

import asyncio
import os
import httpx
from typing import Any
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, CallToolResult

# OpenShift MCP API URL - ZORUNLU
API_URL = os.getenv("MCP_API_URL")
if not API_URL:
    raise ValueError("MCP_API_URL environment variable is required!")

server = Server("zabbix-mcp-proxy")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """OpenShift'ten tool listesini çeker"""
    try:
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            response = await client.get(f"{API_URL}/mcp/tools")
            response.raise_for_status()
            data = response.json()
            
            return [
                Tool(
                    name=t["name"],
                    description=t["description"],
                    inputSchema=t["inputSchema"]
                )
                for t in data.get("tools", [])
            ]
    except Exception as e:
        return [
            Tool(
                name="error",
                description=f"OpenShift bağlantı hatası: {str(e)}",
                inputSchema={"type": "object", "properties": {}, "required": []}
            )
        ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Tool çağrısını OpenShift'e iletir"""
    try:
        async with httpx.AsyncClient(verify=False, timeout=60) as client:
            response = await client.post(
                f"{API_URL}/mcp/message",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments}
                }
            )
            response.raise_for_status()
            result = response.json()
        
        if "error" in result:
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=f"Hata: {result['error'].get('message', 'Unknown error')}"
                )]
            )
        
        contents = result.get("result", {}).get("content", [])
        text_parts = [c.get("text", "") for c in contents if c.get("type") == "text"]
        
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(text_parts) or "Sonuç yok")]
        )
    
    except httpx.HTTPError as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"API bağlantı hatası: {str(e)}")]
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
