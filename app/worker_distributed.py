import time, signal
from config_loader import load_config
from cache import TTLCache
from chunk import chunk_list
from analyzer import analyze
from rate_limiter import RateLimiter
from zabbix_client import ZabbixClient

running = True

def stop(sig, frame):
    global running
    running = False

signal.signal(signal.SIGTERM, stop)

def main():
    cfg = load_config()
    cache = TTLCache(cfg["cache"]["ttl_sec"])

    rl = RateLimiter(10, 1)
    client = ZabbixClient(cfg["zabbix"], rl)
    client.login()

    while running:
        groups = client.get_groups()

        for g in groups:
            hosts = client.get_hosts(g["groupid"])
            if not hosts:
                continue

            host_ids = [h["hostid"] for h in hosts]
            items_all, triggers_all = [], []

            for chunk in chunk_list(host_ids, 50):
                items_all += client.get_items(chunk, 100)
                triggers_all += client.get_triggers(chunk)

            result = analyze(g["name"], hosts, items_all, triggers_all)
            cache.set(g["name"], result)

        time.sleep(30)

if __name__ == "__main__":
    main()