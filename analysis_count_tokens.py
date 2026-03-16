#!/usr/bin/env python3
import json
from pathlib import Path
from collections import Counter, defaultdict

import tiktoken

SOURCE = Path("exports")
DEFAULT_MODEL = "gpt-4o-mini"

SKIP_ROLES = {"system", "tool"}
SKIP_CONTENT_TYPES = {
    "thoughts",
    "reasoning_recap",
    "user_editable_context",
    "tether_browsing_display",
    "tether_quote",
    "code",
}

ENCODING_CACHE = {}


def get_encoding(model: str):
    if model not in ENCODING_CACHE:
        try:
            ENCODING_CACHE[model] = tiktoken.encoding_for_model(model)
        except KeyError:
            ENCODING_CACHE[model] = tiktoken.get_encoding("cl100k_base")
    return ENCODING_CACHE[model]


def message_text(content: dict) -> str:
    ct = (content or {}).get("content_type", "")

    if ct == "text":
        parts = content.get("parts", [])
        return "\n".join(p for p in parts if isinstance(p, str)).strip()

    if ct == "code":
        body = (content.get("text") or "").strip()
        lang = (content.get("language") or "").strip()
        return f"```{lang}\n{body}\n```" if body else ""

    if ct == "multimodal_text":
        chunks = []
        for p in content.get("parts", []):
            if isinstance(p, str):
                chunks.append(p)
            elif isinstance(p, dict):
                if p.get("content_type") == "image_asset_pointer":
                    continue
                chunks.append(p.get("text") or p.get("content") or "")
        return "\n".join(c for c in chunks if c).strip()

    return ""


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


def detect_model(conv: dict, msg: dict) -> str:
    candidates = [
        (msg.get("metadata") or {}).get("model_slug"),
        (msg.get("metadata") or {}).get("default_model_slug"),
        (conv.get("metadata") or {}).get("model_slug"),
        (conv.get("metadata") or {}).get("default_model_slug"),
        conv.get("model"),
    ]
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return DEFAULT_MODEL


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

                text = message_text(content)
                if not text:
                    continue

                model = detect_model(conv, msg)
                yield role, model, text, file


def main():
    totals = Counter()
    by_model = Counter()
    by_role_and_model = defaultdict(Counter)

    message_count = 0
    conversation_count = 0

    if SOURCE.is_dir():
        files = list(SOURCE.rglob("conversations*.json"))
        files.sort(key=lambda p: (p.parent.name, p.name))
    else:
        files = [SOURCE]

    for file in files:
        with open(file, encoding="utf-8") as f:
            data = json.load(f)
        conversation_count += len(data)

    for role, model, text, _file in iter_messages(SOURCE):
        enc = get_encoding(model)
        n = len(enc.encode(text))

        totals["total"] += n
        totals[role] += n
        by_model[model] += n
        by_role_and_model[model][role] += n
        message_count += 1

    print(f"Conversation files scanned: {len(files)}")
    print(f"Conversations found: {conversation_count}")
    print(f"Messages counted: {message_count}")
    print()
    print(f"Estimated total tokens:     {totals['total']:,}")
    print(f"Estimated user tokens:      {totals['user']:,}")
    print(f"Estimated assistant tokens: {totals['assistant']:,}")
    print()
    print("Estimated tokens by model:")
    for model, n in by_model.most_common():
        user_n = by_role_and_model[model]["user"]
        assistant_n = by_role_and_model[model]["assistant"]
        print(f"  {model}: {n:,}  (user: {user_n:,}, assistant: {assistant_n:,})")


if __name__ == "__main__":
    main()