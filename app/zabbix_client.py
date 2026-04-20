import requests

class ZabbixClient:
    def __init__(self, cfg, rl):
        self.url = cfg["url"]
        self.user = cfg["user"]
        self.password = cfg["password"]
        self.auth = None
        self.id = 0
        self._post = rl.wrap(self._post)

    def _post(self, payload):
        r = requests.post(self.url, json=payload, timeout=10)
        r.raise_for_status()
        return r.json()

    def call(self, method, params):
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "auth": self.auth,
            "id": self.id
        }
        self.id += 1
        return self._post(payload)["result"]

    def login(self):
        self.auth = self.call("user.login", {
            "username": self.user,
            "password": self.password
        })

    def get_groups(self):
        return self.call("hostgroup.get", {"output": ["groupid", "name"]})

    def get_hosts(self, gid):
        return self.call("host.get", {
            "groupids": gid,
            "output": ["hostid", "name"]
        })

    def get_items(self, hostids, limit):
        return self.call("item.get", {
            "hostids": hostids,
            "output": ["itemid", "state", "status"],
            "limit": limit
        })

    def get_triggers(self, hostids):
        return self.call("trigger.get", {
            "hostids": hostids,
            "output": ["triggerid", "value"]
        })