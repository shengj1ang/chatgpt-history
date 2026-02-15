#!/usr/bin/env python3
"""
Minimal HTTP server for the ChatGPT History Viewer.

Routes
------
GET /                          → static/index.html
GET /static/<file>             → static/<file>
GET /api/conversations         → JSON list (supports ?q=, ?limit=, ?offset=)
GET /api/conversation/<id>     → JSON conversation + messages
"""
import json
import sqlite3
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

STATIC = Path(__file__).parent / "static"
MIME   = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}


# ── DB helpers ────────────────────────────────────────────────────────────────


def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def fts_query(q: str) -> str:
    """Wrap each whitespace-separated token in quotes for safe FTS5 MATCH."""
    return " ".join(f'"{w}"' for w in q.split() if w)


# ── Request handler ───────────────────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    # Set by serve() before the server starts
    db_path: Path

    def log_message(self, *_):  # silence default access log
        pass

    # ── Response helpers ──────────────────────────────────────────────────────

    def send_json(self, obj: object, status: int = 200) -> None:
        body = json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type",   "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path) -> None:
        data = path.read_bytes()
        mime = MIME.get(path.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type",   mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── Routing ───────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path
        qs     = dict(urllib.parse.parse_qsl(parsed.query))

        if path in ("/", "/index.html"):
            self.send_file(STATIC / "index.html")

        elif path.startswith("/static/"):
            name = path[len("/static/"):]
            # Prevent path traversal
            target = (STATIC / name).resolve()
            if target.is_relative_to(STATIC.resolve()) and target.is_file():
                self.send_file(target)
            else:
                self.send_error(404)

        elif path == "/api/conversations":
            self._api_list(qs)

        elif path.startswith("/api/conversation/"):
            raw_id = path[len("/api/conversation/"):]
            self._api_detail(urllib.parse.unquote(raw_id))

        else:
            self.send_error(404)

    # ── API: conversation list / search ───────────────────────────────────────

    def _api_list(self, qs: dict) -> None:
        q      = qs.get("q", "").strip()
        limit  = min(int(qs.get("limit",  50)), 200)
        offset = max(int(qs.get("offset",  0)),   0)

        conn = open_db(self.db_path)
        try:
            if q:
                fq = fts_query(q)
                rows = conn.execute(
                    """
                    SELECT c.id, c.title, c.create_time, c.update_time,
                           c.message_count,
                           snippet(search_index, -1,
                                   '<mark>', '</mark>', '…', 28) AS snippet
                    FROM   search_index
                    JOIN   conversations c ON c.id = search_index.conversation_id
                    WHERE  search_index MATCH ?
                    ORDER  BY rank
                    LIMIT  ? OFFSET ?
                    """,
                    (fq, limit, offset),
                ).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) FROM search_index WHERE search_index MATCH ?",
                    (fq,),
                ).fetchone()[0]
            else:
                rows = conn.execute(
                    """
                    SELECT id, title, create_time, update_time,
                           message_count, preview AS snippet
                    FROM   conversations
                    ORDER  BY update_time DESC
                    LIMIT  ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) FROM conversations"
                ).fetchone()[0]

            self.send_json({"total": total, "conversations": [dict(r) for r in rows]})

        except sqlite3.OperationalError as exc:
            self.send_json({"total": 0, "conversations": [], "error": str(exc)})
        finally:
            conn.close()

    # ── API: single conversation ──────────────────────────────────────────────

    def _api_detail(self, conv_id: str) -> None:
        conn = open_db(self.db_path)
        try:
            conv = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conv_id,)
            ).fetchone()
            if not conv:
                self.send_error(404)
                return

            msgs = conn.execute(
                "SELECT role, content, create_time FROM messages "
                "WHERE conversation_id = ? ORDER BY seq",
                (conv_id,),
            ).fetchall()

            self.send_json({
                "conversation": dict(conv),
                "messages":     [dict(m) for m in msgs],
            })
        finally:
            conn.close()


# ── Entry point ───────────────────────────────────────────────────────────────


def serve(port: int = 8000, db_path: Path = Path("history.db")) -> None:
    Handler.db_path = db_path
    httpd = HTTPServer(("127.0.0.1", port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    serve()
