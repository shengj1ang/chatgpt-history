# ChatGPT History Viewer

Browse and full-text search your exported ChatGPT conversation history in a local web app.

**No third-party packages required** — Python 3.9+ and SQLite only.

![ChatGPT History Viewer](chatgpt-history.png)

![DALL-E Gallery](dall-e-history.jpg)

---

## Setup

1. **Export your ChatGPT data**
   - Go to ChatGPT → Settings → Data Controls → Export
   - Unzip the archive and place `conversations.json` inside a `source/` directory:

   ```
   chatgpt-history/
   ├── source/
   │   └── conversations.json   ← your export
   ├── app.py
   └── ...
   ```

2. **Run**

   ```bash
   python3 app.py
   ```

   On first run the script builds a local SQLite search index (`history.db`).
   This takes roughly 20–40 seconds for a large export (~1,700 conversations).
   Subsequent starts are instant.

3. Open **http://127.0.0.1:8000** (opened automatically).

---

## Features

- **Full-text search** — Porter-stemmed FTS5 index across all message content
- **Conversation list** — sorted by most-recently updated, paginated with Load more
- **Conversation view** — messages rendered as markdown (headers, code blocks, lists, links, tables)
- **Keyboard navigation** — `/` focus search · `↑`/`↓` move through list · `Esc` clear search
- **Fully offline** — no CDN, no internet required after setup

---

## Rebuilding the index

If you import new history or want to start fresh:

```bash
rm history.db && python3 app.py
```

Or run the indexer directly with custom paths:

```bash
python3 build_db.py --source path/to/conversations.json --db history.db
```

---

## File overview

| File | Purpose |
|------|---------|
| `app.py` | Entry point — builds index if needed, starts server, opens browser |
| `build_db.py` | Parses `conversations.json` → SQLite + FTS5 index |
| `server.py` | stdlib HTTP server with a small JSON API |
| `static/index.html` | App shell |
| `static/style.css` | Dark theme styles |
| `static/app.js` | Frontend logic + self-contained markdown renderer |

---

## API

The server exposes two endpoints consumed by the frontend:

```
GET /api/conversations?q=<query>&limit=50&offset=0
GET /api/conversation/<id>
```

---

## Adapting to other exports

The parser in `build_db.py` expects the standard ChatGPT export format — a JSON
array of conversation objects each containing `id`, `title`, `create_time`,
`update_time`, `current_node`, and a `mapping` dict of message nodes.

To support a different format, modify `parse_conversation()` and `message_text()`
in `build_db.py`. The server and frontend are format-agnostic.
