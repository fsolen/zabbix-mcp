import os
import yaml

def load_config():
    path = os.getenv("CONFIG_PATH", "config.yaml")
    with open(path) as f:
        cfg = yaml.safe_load(f)

    # inject secrets
    cfg["zabbix"]["user"] = os.getenv("ZABBIX_USER")
    cfg["zabbix"]["password"] = os.getenv("ZABBIX_PASS")

    return cfg