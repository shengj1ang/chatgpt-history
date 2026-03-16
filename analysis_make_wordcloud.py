#!/usr/bin/env python3
import json
import re
from collections import Counter
from pathlib import Path

import jieba
from wordcloud import WordCloud

SOURCE = Path("exports")
OUTPUT_IMAGE = "wordcloud.png"
MAX_WORDS = 300
MIN_FREQ = 10

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
    "我们", "你们", "他们", "这个", "那个", "然后", "就是", "因为", "所以",
    "一个", "一些", "一下", "已经", "还是", "如果", "可以", "需要", "觉得",
    "看看", "帮我", "告诉", "这里", "那里", "自己", "不是", "没有", "什么",
    "怎么", "这样", "那样", "一下子", "的话", "的话呢", "一下吧", "一下啊",
    "以及", "进行", "有关", "对于", "通过", "一个个", "一种", "这种", "那个",
    "chatgpt", "gpt", "assistant", "user", "python", "flask", "html", "css",
    "js", "javascript", "json", "markdown", "code", "text", "content", "role",
    "message", "messages", "conversation", "conversations",
    "的", "了", "和", "是", "在", "我", "你", "他", "她", "它", "也", "都",
    "很", "就", "又", "还", "把", "被", "让", "给", "跟", "与", "及", "并",
    "但", "而", "而且", "或者", "一个", "一种", "一些", "这", "那", "吗", "呢",
    "啊", "呀", "吧", "哦", "么", "着", "呢", "去", "来", "上", "下", "里",
    "中", "后", "前", "会", "能", "要", "想", "用", "做", "说", "写", "改",
    "把", "给", "从", "到", "对", "再", "更", "最", "太", "挺", "比较", "非常"
}

STOPWORDS.update({
    "the", "and", "to", "of", "in", "for", "if", "is", "on", "this", "as",
    "from", "not", "or", "with", "that", "you", "are", "by", "it",
    "self", "return", "data", "value", "time", "path", "file", "image",
    "error", "const", "import", "def", "id", "color", "frame", "dt",
    "frac", "__","---","------"
})
TOKEN_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_+-]{2,}")


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


def iter_texts(source: Path):
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

            for msg in thread:
                role = (msg.get("author") or {}).get("role", "")
                if role in SKIP_ROLES:
                    continue

                content = msg.get("content") or {}
                if content.get("content_type") in SKIP_CONTENT_TYPES:
                    continue

                text = message_text(content)
                if text:
                    yield text


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
        if not word:
            continue
        if word in STOPWORDS:
            continue
        if len(word) < 2:
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", word) and word.lower() in {
            "self", "return", "const", "import", "def", "file", "data",
            "value", "time", "path", "image", "error", "color", "frame",
            "true", "false", "null"
        }:
            continue
        if not TOKEN_RE.fullmatch(word):
            continue
        yield word


def main():
    counter = Counter()

    for text in iter_texts(SOURCE):
        counter.update(tokenize(text))

    counter = Counter({k: v for k, v in counter.items() if v >= MIN_FREQ})

    if not counter:
        print("No words found.")
        return

    print("\nTop 100 words:\n")
    for word, freq in counter.most_common(100):
        print(f"{word}\t{freq}")

    wc = WordCloud(
        width=1600,
        height=1000,
        background_color="white",
        max_words=MAX_WORDS,
        collocations=False,
        font_path="SmileySans-Oblique.ttf"
    )

    wc.generate_from_frequencies(counter)
    wc.to_file(OUTPUT_IMAGE)

    print(f"\nSaved word cloud to: {OUTPUT_IMAGE}")


if __name__ == "__main__":
    main()