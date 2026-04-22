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

    async def get_global_stats(self):
        """Get global Zabbix statistics (unique counts)"""
        # Total unique hosts
        total_hosts = await self.call("host.get", {"countOutput": True})
        
        # Total unique items  
        total_items = await self.call("item.get", {"countOutput": True})
        
        # Unsupported items
        unsupported_items = await self.call("item.get", {
            "filter": {"state": 1},
            "countOutput": True
        })
        
        # Total triggers
        total_triggers = await self.call("trigger.get", {"countOutput": True})
        
        # Active (problem) triggers
        active_triggers = await self.call("trigger.get", {
            "filter": {"value": 1},
            "countOutput": True
        })
        
        # Total host groups
        total_groups = await self.call("hostgroup.get", {"countOutput": True})
        
        return {
            "hosts": int(total_hosts) if total_hosts else 0,
            "items": int(total_items) if total_items else 0,
            "unsupported": int(unsupported_items) if unsupported_items else 0,
            "triggers": int(total_triggers) if total_triggers else 0,
            "active_triggers": int(active_triggers) if active_triggers else 0,
            "groups": int(total_groups) if total_groups else 0
        }

    # ==================== QUERY METHODS ====================
    
    async def api_version(self):
        """Get Zabbix API version"""
        return await self.call("apiinfo.version", [])

    async def host_get(self, groupids=None, templateids=None, proxyids=None, 
                       search=None, filter_dict=None, limit=100):
        """Get hosts with flexible filtering"""
        params = {
            "output": ["hostid", "host", "name", "status", "description"],
            "selectGroups": ["groupid", "name"],
            "selectInterfaces": ["interfaceid", "ip", "dns", "type"],
            "selectParentTemplates": ["templateid", "name"],
            "limit": limit
        }
        if groupids:
            params["groupids"] = groupids
        if templateids:
            params["templateids"] = templateids
        if proxyids:
            params["proxyids"] = proxyids
        if search:
            params["search"] = {"name": search}
            params["searchWildcardsEnabled"] = True
        if filter_dict:
            params["filter"] = filter_dict
        return await self.call("host.get", params)

    async def hostgroup_get(self, search=None, filter_dict=None, limit=500):
        """Get host groups with filtering"""
        params = {
            "output": ["groupid", "name"],
            "selectHosts": "count",
            "limit": limit
        }
        if search:
            params["search"] = {"name": search}
            params["searchWildcardsEnabled"] = True
        if filter_dict:
            params["filter"] = filter_dict
        return await self.call("hostgroup.get", params)

    async def template_get(self, search=None, hostids=None, limit=200):
        """Get templates with filtering"""
        params = {
            "output": ["templateid", "host", "name", "description"],
            "selectHosts": "count",
            "selectItems": "count",
            "selectTriggers": "count",
            "limit": limit
        }
        if search:
            params["search"] = {"name": search}
            params["searchWildcardsEnabled"] = True
        if hostids:
            params["hostids"] = hostids
        return await self.call("template.get", params)

    async def item_get(self, hostids=None, groupids=None, templateids=None, 
                       search=None, filter_dict=None, limit=500):
        """Get items with filtering"""
        params = {
            "output": ["itemid", "name", "key_", "type", "value_type", "state", 
                      "status", "lastvalue", "lastclock", "units", "delay"],
            "selectHosts": ["hostid", "name"],
            "limit": limit
        }
        if hostids:
            params["hostids"] = hostids
        if groupids:
            params["groupids"] = groupids
        if templateids:
            params["templateids"] = templateids
        if search:
            params["search"] = {"name": search}
            params["searchWildcardsEnabled"] = True
        if filter_dict:
            params["filter"] = filter_dict
        return await self.call("item.get", params)

    async def trigger_get(self, hostids=None, groupids=None, templateids=None,
                          only_problems=False, min_severity=None, limit=500):
        """Get triggers with filtering"""
        params = {
            "output": ["triggerid", "description", "expression", "priority", 
                      "value", "status", "state", "lastchange"],
            "selectHosts": ["hostid", "name"],
            "expandDescription": True,
            "limit": limit
        }
        if hostids:
            params["hostids"] = hostids
        if groupids:
            params["groupids"] = groupids
        if templateids:
            params["templateids"] = templateids
        if only_problems:
            params["only_true"] = True
        if min_severity is not None:
            params["min_severity"] = min_severity
        return await self.call("trigger.get", params)

    async def problem_get(self, hostids=None, groupids=None, min_severity=None,
                          time_from=None, acknowledged=None, limit=500):
        """Get current problems"""
        params = {
            "output": ["eventid", "objectid", "clock", "name", "severity", 
                      "acknowledged", "suppressed"],
            "selectHosts": ["hostid", "name"],
            "selectTags": "extend",
            "recent": True,
            "sortfield": ["eventid"],
            "sortorder": "DESC",
            "limit": limit
        }
        if hostids:
            params["hostids"] = hostids
        if groupids:
            params["groupids"] = groupids
        if min_severity is not None:
            params["severities"] = list(range(min_severity, 6))
        if time_from:
            params["time_from"] = time_from
        if acknowledged is not None:
            params["acknowledged"] = acknowledged
        return await self.call("problem.get", params)

    async def event_get(self, hostids=None, objectids=None, time_from=None, 
                        time_till=None, limit=500):
        """Get events with filtering"""
        params = {
            "output": ["eventid", "clock", "value", "acknowledged", "severity", "name"],
            "selectHosts": ["hostid", "name"],
            "selectTags": "extend",
            "sortfield": ["clock"],
            "sortorder": "DESC",
            "limit": limit
        }
        if hostids:
            params["hostids"] = hostids
        if objectids:
            params["objectids"] = objectids
        if time_from:
            params["time_from"] = time_from
        if time_till:
            params["time_till"] = time_till
        return await self.call("event.get", params)

    async def history_get(self, itemids, history_type=0, time_from=None, 
                          time_till=None, limit=1000):
        """Get history data for items"""
        params = {
            "output": "extend",
            "itemids": itemids,
            "history": history_type,
            "sortfield": "clock",
            "sortorder": "DESC",
            "limit": limit
        }
        if time_from:
            params["time_from"] = time_from
        if time_till:
            params["time_till"] = time_till
        return await self.call("history.get", params)

    async def trend_get(self, itemids, time_from=None, time_till=None, limit=500):
        """Get trend data for items"""
        params = {
            "output": "extend",
            "itemids": itemids,
            "limit": limit
        }
        if time_from:
            params["time_from"] = time_from
        if time_till:
            params["time_till"] = time_till
        return await self.call("trend.get", params)

    async def maintenance_get(self, hostids=None, groupids=None, limit=100):
        """Get maintenance periods"""
        params = {
            "output": ["maintenanceid", "name", "active_since", "active_till", 
                      "description"],
            "selectHosts": ["hostid", "name"],
            "selectGroups": ["groupid", "name"],
            "selectTimeperiods": "extend",
            "limit": limit
        }
        if hostids:
            params["hostids"] = hostids
        if groupids:
            params["groupids"] = groupids
        return await self.call("maintenance.get", params)

    async def user_get(self, search=None, limit=100):
        """Get users"""
        params = {
            "output": ["userid", "username", "name", "surname", "roleid"],
            "selectRole": ["roleid", "name"],
            "limit": limit
        }
        if search:
            params["search"] = {"username": search}
        return await self.call("user.get", params)

    async def proxy_get(self, limit=100):
        """Get proxies"""
        params = {
            "output": ["proxyid", "name", "operating_mode", "description", "lastaccess"],
            "selectHosts": "count",
            "limit": limit
        }
        return await self.call("proxy.get", params)

    async def action_get(self, eventsource=None, limit=100):
        """Get actions"""
        params = {
            "output": ["actionid", "name", "eventsource", "status"],
            "selectOperations": "extend",
            "limit": limit
        }
        if eventsource is not None:
            params["filter"] = {"eventsource": eventsource}
        return await self.call("action.get", params)

    async def mediatype_get(self, limit=100):
        """Get media types"""
        return await self.call("mediatype.get", {
            "output": ["mediatypeid", "name", "type", "status", "description"],
            "limit": limit
        })

    async def script_get(self, hostid=None, limit=100):
        """Get scripts"""
        params = {
            "output": ["scriptid", "name", "command", "type", "scope"],
            "limit": limit
        }
        if hostid:
            params["hostids"] = hostid
        return await self.call("script.get", params)

    async def usermacro_get(self, hostids=None, globalmacroids=None, limit=500):
        """Get user macros"""
        params = {
            "output": "extend",
            "selectHosts": ["hostid", "name"],
            "limit": limit
        }
        if hostids:
            params["hostids"] = hostids
        if globalmacroids:
            params["globalmacroids"] = globalmacroids
        return await self.call("usermacro.get", params)

    async def graph_get(self, hostids=None, groupids=None, search=None, limit=200):
        """Get graphs"""
        params = {
            "output": ["graphid", "name", "width", "height", "graphtype"],
            "selectHosts": ["hostid", "name"],
            "limit": limit
        }
        if hostids:
            params["hostids"] = hostids
        if groupids:
            params["groupids"] = groupids
        if search:
            params["search"] = {"name": search}
        return await self.call("graph.get", params)

    async def discoveryrule_get(self, hostids=None, limit=200):
        """Get LLD discovery rules"""
        params = {
            "output": ["itemid", "name", "key_", "type", "status", "delay"],
            "selectHosts": ["hostid", "name"],
            "limit": limit
        }
        if hostids:
            params["hostids"] = hostids
        return await self.call("discoveryrule.get", params)

    async def sla_get(self, limit=100):
        """Get SLAs"""
        return await self.call("sla.get", {
            "output": "extend",
            "selectServiceTags": "extend",
            "limit": limit
        })

    async def service_get(self, limit=200):
        """Get services"""
        return await self.call("service.get", {
            "output": ["serviceid", "name", "status", "algorithm"],
            "selectParents": ["serviceid", "name"],
            "selectChildren": ["serviceid", "name"],
            "limit": limit
        })

    # ==================== MANAGEMENT METHODS (require write permission) ====================

    async def host_create(self, name, groupids, interfaces=None, templateids=None, 
                          description=None):
        """Create a new host"""
        params = {
            "host": name,
            "groups": [{"groupid": gid} for gid in groupids]
        }
        if interfaces:
            params["interfaces"] = interfaces
        else:
            # Default agent interface
            params["interfaces"] = [{
                "type": 1, "main": 1, "useip": 1, "ip": "127.0.0.1", 
                "dns": "", "port": "10050"
            }]
        if templateids:
            params["templates"] = [{"templateid": tid} for tid in templateids]
        if description:
            params["description"] = description
        return await self.call("host.create", params)

    async def host_update(self, hostid, **kwargs):
        """Update host properties"""
        params = {"hostid": hostid}
        params.update(kwargs)
        return await self.call("host.update", params)

    async def host_delete(self, hostids):
        """Delete hosts"""
        return await self.call("host.delete", hostids)

    async def hostgroup_create(self, name):
        """Create a host group"""
        return await self.call("hostgroup.create", {"name": name})

    async def hostgroup_update(self, groupid, name):
        """Update host group"""
        return await self.call("hostgroup.update", {"groupid": groupid, "name": name})

    async def hostgroup_delete(self, groupids):
        """Delete host groups"""
        return await self.call("hostgroup.delete", groupids)

    async def maintenance_create(self, name, active_since, active_till, groupids=None,
                                  hostids=None, description=None):
        """Create maintenance period"""
        params = {
            "name": name,
            "active_since": active_since,
            "active_till": active_till,
            "timeperiods": [{"timeperiod_type": 0}]
        }
        if groupids:
            params["groups"] = [{"groupid": gid} for gid in groupids]
        if hostids:
            params["hosts"] = [{"hostid": hid} for hid in hostids]
        if description:
            params["description"] = description
        return await self.call("maintenance.create", params)

    async def maintenance_delete(self, maintenanceids):
        """Delete maintenance periods"""
        return await self.call("maintenance.delete", maintenanceids)

    async def event_acknowledge(self, eventids, message=None, action=1):
        """Acknowledge events. Action: 1=close, 2=ack, 4=add message, 8=change severity"""
        params = {"eventids": eventids, "action": action}
        if message:
            params["message"] = message
        return await self.call("event.acknowledge", params)

    async def script_execute(self, scriptid, hostid):
        """Execute a script on a host"""
        return await self.call("script.execute", {
            "scriptid": scriptid, 
            "hostid": hostid
        })