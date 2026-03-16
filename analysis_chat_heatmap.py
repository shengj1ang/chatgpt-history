#!/usr/bin/env python3
import json
from pathlib import Path
from collections import Counter
from datetime import datetime

import numpy as np
import matplotlib.pyplot as plt

SOURCE = Path("exports")
OUTPUT = "chat_heatmap.png"

SKIP_ROLES = {"system", "tool"}
SKIP_CONTENT_TYPES = {
    "thoughts",
    "reasoning_recap",
    "user_editable_context",
    "tether_browsing_display",
    "tether_quote",
    "code",
}


def extract_thread(mapping: dict, current_node: str) -> list:
    path, seen = [], set()
    node_id = current_node
    while node_id and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        node = mapping[node_id]
        if node.get("message"):
            path.append(node["message"])
        node_id = node.get("parent")
    path.reverse()
    return path


def iter_messages(source: Path):
    if source.is_dir():
        files = list(source.rglob("conversations*.json"))
        files.sort(key=lambda p: (p.parent.name, p.name))
    else:
        files = [source]

    for file in files:
        with open(file, encoding="utf-8") as f:
            data = json.load(f)

        for conv in data:
            mapping = conv.get("mapping") or {}
            current_node = conv.get("current_node")
            if not current_node:
                continue

            thread = list(reversed(extract_thread(mapping, current_node)))

            for msg in thread:
                role = (msg.get("author") or {}).get("role", "")
                if role in SKIP_ROLES:
                    continue

                content = msg.get("content") or {}
                if content.get("content_type") in SKIP_CONTENT_TYPES:
                    continue

                ts = msg.get("create_time")
                if ts:
                    yield role, ts


def main():
    heatmap = np.zeros((7, 24), dtype=int)
    role_counter = Counter()
    total = 0

    for role, ts in iter_messages(SOURCE):
        dt = datetime.fromtimestamp(ts)
        weekday = dt.weekday()   # Monday=0
        hour = dt.hour
        heatmap[weekday, hour] += 1
        role_counter[role] += 1
        total += 1

    print(f"Total messages counted: {total}")
    for role, n in role_counter.items():
        print(f"{role}: {n}")

    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    plt.figure(figsize=(16, 6))
    plt.imshow(heatmap, aspect="auto", interpolation="nearest")
    plt.xticks(range(24), [f"{h:02d}:00" for h in range(24)], rotation=45)
    plt.yticks(range(7), days)
    plt.xlabel("Hour of day")
    plt.ylabel("Day of week")
    plt.title("Chat activity heatmap")
    plt.colorbar(label="Message count")
    plt.tight_layout()
    plt.savefig(OUTPUT, dpi=200)
    plt.show()

    busiest = np.unravel_index(np.argmax(heatmap), heatmap.shape)
    print(f"\nBusiest time slot: {days[busiest[0]]} {busiest[1]:02d}:00 - {busiest[1]:02d}:59")


if __name__ == "__main__":
    main()
