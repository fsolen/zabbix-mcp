"""
Multi-Server Zabbix Client Manager

Manages multiple ZabbixClient instances for different Zabbix servers.
Supports:
- Individual server queries
- Global queries across all servers
- Server-specific statistics
"""
import asyncio
import structlog
from typing import Dict, List, Optional, Any
from .zabbix_client import ZabbixClient
from .config_loader import load_config, get_server_names

logger = structlog.get_logger()


class ZabbixClientManager:
    """Manages multiple Zabbix server connections"""
    
    def __init__(self, rate_limiter=None):
        self.config = load_config()
        self.rate_limiter = rate_limiter
        self.clients: Dict[str, ZabbixClient] = {}
        self.default_server = self.config.get("default_server", "default")
        self._initialized = False
    
    async def initialize(self):
        """Initialize all Zabbix clients and login"""
        if self._initialized:
            return
        
        servers = self.config.get("servers", {})
        
        for server_name, server_cfg in servers.items():
            try:
                client = ZabbixClient(server_cfg, self.rate_limiter)
                await client.login()
                self.clients[server_name] = client
                logger.info("zabbix_server_connected", 
                           server=server_name, 
                           url=server_cfg.get("url"))
            except Exception as e:
                logger.error("zabbix_server_connection_failed",
                           server=server_name,
                           error=str(e))
        
        self._initialized = True
        logger.info("zabbix_manager_initialized", 
                   connected_servers=list(self.clients.keys()))
    
    def get_client(self, server: Optional[str] = None) -> ZabbixClient:
        """Get a specific server's client or the default one"""
        server_name = server or self.default_server
        
        if server_name not in self.clients:
            available = list(self.clients.keys())
            raise ValueError(f"Server '{server_name}' not found. Available: {available}")
        
        return self.clients[server_name]
    
    def get_all_clients(self) -> Dict[str, ZabbixClient]:
        """Get all connected clients"""
        return self.clients
    
    def get_server_names(self) -> List[str]:
        """Get list of connected server names"""
        return list(self.clients.keys())
    
    async def call_on_server(self, server: str, method: str, *args, **kwargs) -> Any:
        """Call a method on a specific server's client"""
        client = self.get_client(server)
        method_fn = getattr(client, method, None)
        if method_fn is None:
            raise ValueError(f"Method '{method}' not found on ZabbixClient")
        return await method_fn(*args, **kwargs)
    
    async def call_on_all(self, method: str, *args, **kwargs) -> Dict[str, Any]:
        """
        Call a method on all servers and return combined results.
        Returns: {server_name: result}
        """
        results = {}
        tasks = []
        
        for server_name, client in self.clients.items():
            method_fn = getattr(client, method, None)
            if method_fn:
                tasks.append((server_name, method_fn(*args, **kwargs)))
        
        for server_name, task in tasks:
            try:
                results[server_name] = await task
            except Exception as e:
                logger.error("zabbix_global_call_error",
                           server=server_name,
                           method=method,
                           error=str(e))
                results[server_name] = {"error": str(e)}
        
        return results
    
    async def get_global_stats(self) -> Dict[str, Any]:
        """Get statistics from all servers combined"""
        all_stats = await self.call_on_all("get_global_stats")
        
        combined = {
            "servers": {},
            "totals": {
                "total_hosts": 0,
                "enabled_hosts": 0,
                "disabled_hosts": 0,
                "total_items": 0,
                "enabled_items": 0,
                "unsupported_items": 0,
                "total_triggers": 0,
                "problem_triggers": 0,
                "total_users": 0,
                "total_hostgroups": 0
            }
        }
        
        for server_name, stats in all_stats.items():
            if "error" in stats:
                combined["servers"][server_name] = {"status": "error", "error": stats["error"]}
                continue
            
            combined["servers"][server_name] = {
                "status": "ok",
                **stats
            }
            
            # Sum up totals
            for key in combined["totals"]:
                if key in stats:
                    combined["totals"][key] += stats.get(key, 0)
        
        return combined
    
    async def get_all_problems(self, min_severity: int = 0, limit_per_server: int = 100) -> List[Dict]:
        """Get problems from all servers with server name tagged"""
        all_results = await self.call_on_all("problem_get", 
                                              min_severity=min_severity, 
                                              limit=limit_per_server)
        
        combined = []
        for server_name, problems in all_results.items():
            if isinstance(problems, dict) and "error" in problems:
                continue
            if isinstance(problems, list):
                for problem in problems:
                    problem["_server"] = server_name
                combined.extend(problems)
        
        # Sort by severity (descending) and eventid
        combined.sort(key=lambda x: (-int(x.get("severity", 0)), -int(x.get("eventid", 0))))
        
        return combined
    
    async def search_hosts_global(self, pattern: str, limit_per_server: int = 50) -> List[Dict]:
        """Search for hosts across all servers"""
        all_results = await self.call_on_all("host_get", 
                                              search={"name": pattern},
                                              limit=limit_per_server)
        
        combined = []
        for server_name, hosts in all_results.items():
            if isinstance(hosts, dict) and "error" in hosts:
                continue
            if isinstance(hosts, list):
                for host in hosts:
                    host["_server"] = server_name
                combined.extend(hosts)
        
        return combined
    
    async def close(self):
        """Close all client connections"""
        for server_name, client in self.clients.items():
            try:
                await client.close()
            except Exception as e:
                logger.warning("zabbix_client_close_error",
                             server=server_name,
                             error=str(e))
        self.clients = {}
        self._initialized = False


# Global manager instance
_manager: Optional[ZabbixClientManager] = None


async def get_zabbix_manager(rate_limiter=None) -> ZabbixClientManager:
    """Get or create the global ZabbixClientManager instance"""
    global _manager
    
    if _manager is None:
        _manager = ZabbixClientManager(rate_limiter)
        await _manager.initialize()
    
    return _manager


async def close_zabbix_manager():
    """Close the global manager"""
    global _manager
    if _manager:
        await _manager.close()
        _manager = None
