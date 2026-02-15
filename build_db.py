#!/usr/bin/env python3
"""
Parse source/conversations.json → SQLite with FTS5.

Schema
------
conversations(id, title, create_time, update_time, message_count, preview)
messages(id, conversation_id, role, content, create_time, seq)
search_index [fts5](conversation_id UNINDEXED, title, body)

Can also be run directly:
    python3 build_db.py [--source source/conversations.json] [--db history.db]
"""
import json
import sqlite3
import sys
from pathlib import Path

# ── Content-type handling ──────────────────────────────────────────────────────

# Roles whose messages we never show
SKIP_ROLES = frozenset({"system", "tool"})

# Content types we silently ignore
SKIP_CONTENT_TYPES = frozenset({
    "thoughts",
    "reasoning_recap",
    "user_editable_context",
    "tether_browsing_display",
    "tether_quote",
    "code",           # tool invocation JSON (search, browser, etc.) — not user-facing
})


def message_text(content: dict) -> str:
    """Return displayable markdown text from a message content dict."""
    ct = (content or {}).get("content_type", "")

    if ct == "text":
        parts = content.get("parts", [])
        return "\n".join(p for p in parts if isinstance(p, str)).strip()

    if ct == "code":
        # Code interpreter / tool output — stored in content["text"] directly
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
                    ptr = p.get("asset_pointer", "")
                    # Strip scheme and fragment: sediment://file_XXX#fragment → file_XXX
                    file_id = ptr.replace("sediment://", "").split("#")[0]
                    if file_id:
                        chunks.append(f"![image](/source/{file_id})")
                else:
                    chunks.append(p.get("text") or p.get("content") or "")
        return "\n".join(c for c in chunks if c).strip()

    return ""


# ── Thread extraction ─────────────────────────────────────────────────────────


def extract_thread(mapping: dict, current_node: str) -> list:
    """
    Walk from current_node back to root via parent pointers.
    Returns messages in chronological order (root → leaf).
    Handles the branching case: current_node always points to the
    last message of the active branch.
    """
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


# ── Per-conversation parser ───────────────────────────────────────────────────


def parse_conversation(conv: dict):
    """
    Returns (meta_dict, message_list) or (None, []) when the
    conversation has no usable messages.
    """
    cid = conv.get("id") or conv.get("conversation_id")
    if not cid:
        return None, []

    mapping     = conv.get("mapping") or {}
    current_node = conv.get("current_node")
    if not current_node:
        return None, []

    thread = extract_thread(mapping, current_node)

    msgs = []
    for seq, msg in enumerate(thread):
        role = (msg.get("author") or {}).get("role", "")
        if role in SKIP_ROLES:
            continue
        content = msg.get("content") or {}
        if content.get("content_type") in SKIP_CONTENT_TYPES:
            continue
        text = message_text(content)
        if not text:
            continue
        msgs.append({
            "conversation_id": cid,
            "role":            role,
            "content":         text,
            "create_time":     msg.get("create_time") or 0,
            "seq":             seq,
        })

    if not msgs:
        return None, []

    preview = next(
        (m["content"][:300] for m in msgs if m["role"] == "user"), ""
    )
    meta = {
        "id":            cid,
        "title":         (conv.get("title") or "Untitled").strip(),
        "create_time":   conv.get("create_time") or 0,
        "update_time":   conv.get("update_time") or 0,
        "message_count": len(msgs),
        "preview":       preview,
    }
    return meta, msgs


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """\
DROP TABLE IF EXISTS messages;
DROP TABLE IF EXISTS conversations;
DROP TABLE IF EXISTS search_index;

CREATE TABLE conversations (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    create_time   REAL,
    update_time   REAL,
    message_count INTEGER,
    preview       TEXT
);

CREATE TABLE messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role            TEXT,
    content         TEXT,
    create_time     REAL,
    seq             INTEGER
);

CREATE INDEX idx_msg_conv ON messages (conversation_id, seq);

CREATE VIRTUAL TABLE search_index USING fts5(
    conversation_id UNINDEXED,
    title,
    body,
    tokenize = 'porter unicode61'
);
"""


# ── Build ─────────────────────────────────────────────────────────────────────


def build(source: Path, db_path: Path) -> None:
    print(f"Loading {source} …", flush=True)
    with open(source, encoding="utf-8") as f:
        data = json.load(f)

    total = len(data)
    print(f"{total} conversations found. Indexing…", flush=True)

    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA)

    conv_rows, msg_rows, fts_rows = [], [], []

    for i, conv in enumerate(data):
        if i % 100 == 0:
            sys.stderr.write(f"\r  {i:>5}/{total}")
            sys.stderr.flush()

        meta, msgs = parse_conversation(conv)
        if meta is None:
            continue

        conv_rows.append(meta)
        msg_rows.extend(msgs)
        fts_rows.append((
            meta["id"],
            meta["title"],
            "\n".join(m["content"] for m in msgs),
        ))

    sys.stderr.write(f"\r  {total}/{total}\n")

    db.executemany(
        "INSERT OR REPLACE INTO conversations "
        "VALUES (:id, :title, :create_time, :update_time, :message_count, :preview)",
        conv_rows,
    )
    db.executemany(
        "INSERT INTO messages (conversation_id, role, content, create_time, seq) "
        "VALUES (:conversation_id, :role, :content, :create_time, :seq)",
        msg_rows,
    )
    db.executemany("INSERT INTO search_index VALUES (?, ?, ?)", fts_rows)

    db.commit()
    db.close()
    print(f"Done — {len(conv_rows)} conversations indexed → {db_path}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Build ChatGPT history search database")
    ap.add_argument("--source", default="source/conversations.json",
                    help="Path to conversations.json  (default: source/conversations.json)")
    ap.add_argument("--db",     default="history.db",
                    help="Output SQLite database path  (default: history.db)")
    args = ap.parse_args()
    build(Path(args.source), Path(args.db))
