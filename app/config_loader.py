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

    # Multi-server support
    # Check for new "servers" format vs legacy "zabbix" format
    if "servers" in cfg:
        # New multi-server format
        for server_name, server_cfg in cfg["servers"].items():
            # Inject secrets from environment: ZABBIX_{SERVER}_USER, ZABBIX_{SERVER}_PASS
            env_prefix = server_name.upper().replace("-", "_")
            server_cfg["user"] = os.getenv(f"ZABBIX_{env_prefix}_USER") or os.getenv("ZABBIX_USER")
            server_cfg["password"] = os.getenv(f"ZABBIX_{env_prefix}_PASS") or os.getenv("ZABBIX_PASS")
    elif "zabbix" in cfg:
        # Legacy single-server format - convert to multi-server
        cfg["servers"] = {
            "default": cfg["zabbix"]
        }
        cfg["servers"]["default"]["user"] = os.getenv("ZABBIX_USER")
        cfg["servers"]["default"]["password"] = os.getenv("ZABBIX_PASS")
        cfg["default_server"] = "default"
    
    # Set default server if not specified
    cfg.setdefault("default_server", list(cfg.get("servers", {}).keys())[0] if cfg.get("servers") else "default")
    
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


def get_server_names():
    """Get list of configured server names"""
    cfg = load_config()
    return list(cfg.get("servers", {}).keys())