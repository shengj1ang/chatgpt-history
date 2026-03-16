#!/usr/bin/env python3
import json
import re
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

import numpy as np
import matplotlib.pyplot as plt
import jieba
from wordcloud import WordCloud

SOURCE = Path("exports")
OUTDIR = Path("chatgpt_report")
OUTDIR.mkdir(exist_ok=True)

SKIP_ROLES = {"system", "tool"}
SKIP_CONTENT_TYPES = {
    "thoughts",
    "reasoning_recap",
    "user_editable_context",
    "tether_browsing_display",
    "tether_quote",
    "code",
}

STOPWORDS = {
    "的", "了", "和", "是", "在", "我", "你", "他", "她", "它", "也", "都",
    "很", "就", "又", "还", "把", "被", "让", "给", "跟", "与", "及", "并",
    "但", "而", "而且", "或者", "一个", "一种", "一些", "这", "那", "吗", "呢",
    "啊", "呀", "吧", "哦", "么", "着", "去", "来", "上", "下", "里", "中",
    "后", "前", "会", "能", "要", "想", "用", "做", "说", "写", "改",
    "现在", "可能", "例如", "比如", "是否", "这些", "这是", "一句", "根据",
    "必须", "继续", "而是", "主要", "完全", "提供", "说明", "确保", "不会", "通常",
    "使用", "问题", "方法", "内容", "支持", "建议", "部分", "情况", "结果"
}

TOKEN_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_+\-]{2,}")


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


def iter_conversations(source: Path):
    if source.is_dir():
        files = list(source.rglob("conversations*.json"))
        files.sort(key=lambda p: (p.parent.name, p.name))
    else:
        files = [source]

    for file in files:
        print(f"Loading {file}")
        with open(file, encoding="utf-8") as f:
            data = json.load(f)

        for conv in data:
            mapping = conv.get("mapping") or {}
            current_node = conv.get("current_node")
            if not current_node:
                continue

            thread = list(reversed(extract_thread(mapping, current_node)))
            messages = []

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

                ts = msg.get("create_time") or 0
                messages.append({
                    "role": role,
                    "text": text,
                    "ts": ts,
                })

            if messages:
                yield {
                    "title": conv.get("title") or "Untitled",
                    "messages": messages,
                    "create_time": conv.get("create_time") or 0,
                    "update_time": conv.get("update_time") or 0,
                }


def clean_text(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`[^`\n]+`", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"sandbox:/\S+", " ", text)
    text = re.sub(r"/source/\S+", " ", text)
    text = re.sub(r"!$begin:math:display$\[\^$end:math:display$]*\]$begin:math:text$\[\^\)\]\+$end:math:text$", " ", text)
    text = re.sub(r"$begin:math:display$\[\^$end:math:display$]+\]$begin:math:text$\[\^\)\]\+$end:math:text$", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9_+\-\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def tokenize(text: str):
    text = clean_text(text)

    for word in jieba.cut(text, cut_all=False):
        word = word.strip().lower()
        if not word or len(word) < 2:
            continue
        if word in STOPWORDS:
            continue
        if word.isdigit():
            continue
        if re.fullmatch(r"[-_]+", word):
            continue
        if not TOKEN_RE.fullmatch(word):
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", word) and word.lower() in {
            "self", "return", "const", "import", "def", "file", "data",
            "value", "time", "path", "image", "error", "color", "frame",
            "true", "false", "null", "none", "name", "key", "new", "end",
            "the", "and", "for", "with", "that", "this", "from", "into"
        }:
            continue
        yield word


def pick_font():
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def make_heatmap(all_messages):
    heatmap = np.zeros((7, 24), dtype=int)
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for msg in all_messages:
        dt = datetime.fromtimestamp(msg["ts"])
        heatmap[dt.weekday(), dt.hour] += 1

    plt.figure(figsize=(16, 6))
    plt.imshow(heatmap, aspect="auto", interpolation="nearest")
    plt.xticks(range(24), [f"{h:02d}:00" for h in range(24)], rotation=45)
    plt.yticks(range(7), days)
    plt.xlabel("Hour of day")
    plt.ylabel("Day of week")
    plt.title("Chat activity heatmap")
    plt.colorbar(label="Message count")
    plt.tight_layout()
    plt.savefig(OUTDIR / "heatmap.png", dpi=200)
    plt.close()


def make_wordcloud(user_messages):
    counter = Counter()
    for msg in user_messages:
        counter.update(tokenize(msg["text"]))

    counter = Counter({k: v for k, v in counter.items() if v >= 10})

    if not counter:
        print("No words found for word cloud.")
        return

    font_path = pick_font()

    wc = WordCloud(
        width=1600,
        height=1000,
        background_color="white",
        max_words=300,
        collocations=False,
        font_path=font_path,
    )
    wc.generate_from_frequencies(counter)
    wc.to_file(str(OUTDIR / "wordcloud.png"))

    print("\nTop 50 words:")
    for word, freq in counter.most_common(50):
        print(f"{word}\t{freq}")


def make_conversation_length_hist(conversations):
    lengths = [len(c["messages"]) for c in conversations]

    plt.figure(figsize=(10, 6))
    bins = [1, 5, 10, 20, 50, 100, 200, max(lengths) + 1]
    plt.hist(lengths, bins=bins)
    plt.xlabel("Messages per conversation")
    plt.ylabel("Number of conversations")
    plt.title("Conversation length distribution")
    plt.tight_layout()
    plt.savefig(OUTDIR / "conversation_length.png", dpi=200)
    plt.close()


def make_monthly_usage(all_messages):
    monthly = Counter()
    for msg in all_messages:
        dt = datetime.fromtimestamp(msg["ts"])
        key = f"{dt.year}-{dt.month:02d}"
        monthly[key] += 1

    labels = sorted(monthly.keys())
    values = [monthly[k] for k in labels]

    plt.figure(figsize=(14, 6))
    plt.plot(labels, values, marker="o")
    plt.xticks(rotation=45, ha="right")
    plt.xlabel("Month")
    plt.ylabel("Message count")
    plt.title("Monthly chat usage")
    plt.tight_layout()
    plt.savefig(OUTDIR / "monthly_usage.png", dpi=200)
    plt.close()


def make_summary(conversations, all_messages, user_messages, assistant_messages):
    summary = []
    summary.append(f"Conversations: {len(conversations)}")
    summary.append(f"Messages: {len(all_messages)}")
    summary.append(f"User messages: {len(user_messages)}")
    summary.append(f"Assistant messages: {len(assistant_messages)}")

    if conversations:
        avg_len = sum(len(c["messages"]) for c in conversations) / len(conversations)
        summary.append(f"Average messages per conversation: {avg_len:.2f}")

    if all_messages:
        timestamps = [m["ts"] for m in all_messages if m["ts"]]
        if timestamps:
            first_dt = datetime.fromtimestamp(min(timestamps))
            last_dt = datetime.fromtimestamp(max(timestamps))
            summary.append(f"First message: {first_dt.isoformat(sep=' ', timespec='minutes')}")
            summary.append(f"Last message: {last_dt.isoformat(sep=' ', timespec='minutes')}")

    (OUTDIR / "summary.txt").write_text("\n".join(summary), encoding="utf-8")
    print("\n".join(summary))


def main():
    conversations = list(iter_conversations(SOURCE))
    all_messages = [m for c in conversations for m in c["messages"]]
    user_messages = [m for m in all_messages if m["role"] == "user"]
    assistant_messages = [m for m in all_messages if m["role"] == "assistant"]

    if not conversations:
        print("No conversations found.")
        return

    make_summary(conversations, all_messages, user_messages, assistant_messages)
    make_heatmap(all_messages)
    make_wordcloud(user_messages)
    make_conversation_length_hist(conversations)
    make_monthly_usage(all_messages)

    print(f"\nSaved report files to: {OUTDIR.resolve()}")


if __name__ == "__main__":
    main()