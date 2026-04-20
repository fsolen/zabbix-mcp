def analyze(group, hosts, items, triggers):
    total_items = len(items)
    unsupported = sum(1 for i in items if i["state"] == "1")
    active_triggers = sum(1 for t in triggers if t["value"] == "1")

    noise = active_triggers / len(triggers) if triggers else 0

    recs = []
    if total_items and unsupported / total_items > 0.1:
        recs.append("fix unsupported items")
    if noise > 0.3:
        recs.append("reduce trigger noise")
    if not recs:
        recs.append("healthy")

    return {
        "group": group,
        "metrics": {
            "hosts": len(hosts),
            "items": total_items,
            "unsupported": unsupported,
            "triggers": len(triggers),
            "active_triggers": active_triggers
        },
        "analysis": {"noise_score": round(noise, 2)},
        "recommendations": recs
    }