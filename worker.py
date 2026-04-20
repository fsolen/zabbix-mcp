from chunk import chunk_list
from analyzer import analyze

def run_cycle(client, cfg, cache):
    groups = client.get_groups()
    results = []

    for g in groups:
        hosts = client.get_hosts(g["groupid"])
        if not hosts:
            continue

        all_items = []
        all_triggers = []

        host_ids = [h["hostid"] for h in hosts]

        # 🔥 host chunking (max 50)
        for h_chunk in chunk_list(host_ids, cfg["limits"]["max_hosts_per_query"]):

            # item chunk (limit 100)
            items = client.get_items(
                h_chunk,
                cfg["limits"]["max_items_per_query"]
            )
            triggers = client.get_triggers(h_chunk)

            all_items.extend(items)
            all_triggers.extend(triggers)

        result = analyze(g["name"], hosts, all_items, all_triggers)
        results.append(result)

    cache.set("analysis", results)
    return results