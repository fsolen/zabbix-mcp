import os
import yaml
import logging
import structlog

_config_cache = None

def load_config():
    global _config_cache
    
    # Return cached config if available
    if _config_cache is not None:
        return _config_cache
    
    path = os.getenv("CONFIG_PATH", "config.yaml")
    with open(path) as f:
        cfg = yaml.safe_load(f)

    # inject secrets from environment
    cfg["zabbix"]["user"] = os.getenv("ZABBIX_USER")
    cfg["zabbix"]["password"] = os.getenv("ZABBIX_PASS")
    
    # Set defaults for new config sections
    cfg.setdefault("mode", {"read_only": True})
    cfg.setdefault("rate_limit", {
        "enabled": True,
        "max_requests": 60,
        "window_minutes": 1,
        "calls_per_second": 5
    })
    cfg.setdefault("logging", {"level": "INFO"})
    cfg.setdefault("disabled_tags", [])
    cfg.setdefault("limits", {
        "max_hosts_per_query": 100,
        "max_items_per_query": 5000,
        "concurrent_groups": 3
    })
    
    # Configure logging level
    log_level = cfg.get("logging", {}).get("level", "INFO").upper()
    logging.basicConfig(level=getattr(logging, log_level, logging.INFO))
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level, logging.INFO)
        )
    )
    
    _config_cache = cfg
    return cfg


def reload_config():
    """Force reload config (useful for testing)"""
    global _config_cache
    _config_cache = None
    return load_config()