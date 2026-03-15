import json
import mimetypes
import re
import sqlite3
import sys
import threading
import webbrowser
import html
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, session, redirect, url_for, render_template
from functools import wraps
from datetime import timedelta

from userconfig import *

ROOT = Path(__file__).parent
STATIC_DIR = ROOT / "static"

_SOURCE_INDEX: dict[str, Path] = {}
_GALLERY_IMAGES: list[dict] = []

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(days=180)


def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def fts_query(q: str) -> str:
    return " ".join(f'"{w}"' for w in q.split() if w)


def build_source_index(source_dir: Path) -> None:
    _SOURCE_INDEX.clear()
    _GALLERY_IMAGES.clear()

    if not source_dir.exists():
        return

    for f in source_dir.rglob("*"):
        if not f.is_file() or not f.name.startswith("file"):
            continue
        _SOURCE_INDEX[f.name] = f

    for dalle_dir in source_dir.rglob("dalle-generations"):
        if not dalle_dir.is_dir():
            continue

        for f in sorted(dalle_dir.rglob("*")):
            if f.is_file() and f.suffix.lower() in (".webp", ".png", ".jpg", ".jpeg"):
                rel = f.relative_to(dalle_dir).as_posix()
                gallery_key = f"__dalle/{rel}"

                _GALLERY_IMAGES.append({
                    "filename": rel,
                    "url": f"/source/{gallery_key}",
                })
                _SOURCE_INDEX[gallery_key] = f

    print(f"Source index: {len(_SOURCE_INDEX)} files, {len(_GALLERY_IMAGES)} gallery images")


def resolve_source_file(file_id: str) -> Path | None:
    if not re.match(r'^[\w\-./]+$', file_id):
        return None

    f = _SOURCE_INDEX.get(file_id)
    if f and f.exists():
        return f

    for name, path in _SOURCE_INDEX.items():
        if name.startswith(file_id) and path.exists():
            return path

    fuzzy_id = file_id
    fuzzy_id = re.sub(r'</?em>', ' ', fuzzy_id, flags=re.IGNORECASE)
    fuzzy_id = html.unescape(fuzzy_id)
    fuzzy_id = re.sub(r'\s+', ' ', fuzzy_id).strip()

    for name, path in _SOURCE_INDEX.items():
        candidate = html.unescape(name)
        if fuzzy_id in candidate and path.exists():
            return path

    return None

'''
Start Of Auth and Login
'''
def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):

        if not AUTH_ENABLED:
            return view(*args, **kwargs)

        if session.get("logged_in"):
            return view(*args, **kwargs)

        return redirect(url_for("login", next=request.path))

    return wrapper


@app.route("/login", methods=["GET","POST"])
def login():

    next_url = request.args.get("next") or request.form.get("next") or "/"
    error=""

    if request.method=="POST":

        if (
            request.form.get("username")==AUTH_USERNAME
            and
            request.form.get("password")==AUTH_PASSWORD
        ):
            session.permanent=True
            session["logged_in"]=True
            return redirect(next_url)

        error="Invalid credentials"

    return render_template("login.html",error=error,next_url=next_url)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

'''
End Of Auth and Login
'''
@app.route("/")
@app.route("/index.html")
@login_required
def index():
    return render_template("index.html")

@app.route("/source/<path:file_id>")
@login_required
def source_file(file_id: str):
    target = resolve_source_file(file_id)
    if not target:
        abort(404)
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return send_file(target, mimetype=mime, max_age=86400)


@app.route("/api/conversations")
@login_required
def api_conversations():
    q = request.args.get("q", "").strip()
    try:
        limit = min(int(request.args.get("limit", 200)), 1000)
    except ValueError:
        limit = 200
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        offset = 0

    conn = open_db(DB_PATH)
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

        return jsonify({
            "total": total,
            "conversations": [dict(r) for r in rows],
        })

    except sqlite3.OperationalError as exc:
        return jsonify({
            "total": 0,
            "conversations": [],
            "error": str(exc),
        })
    finally:
        conn.close()


@app.route("/api/conversation/<path:conv_id>")
@login_required
def api_conversation(conv_id: str):
    conn = open_db(DB_PATH)
    try:
        conv = conn.execute(
            "SELECT * FROM conversations WHERE id = ?",
            (conv_id,),
        ).fetchone()

        if not conv:
            abort(404)

        msgs = conn.execute(
            """
            SELECT role, content, create_time
            FROM messages
            WHERE conversation_id = ?
            ORDER BY seq DESC
            """,
            (conv_id,),
        ).fetchall()

        return jsonify({
            "conversation": dict(conv),
            "messages": [dict(m) for m in msgs],
        })
    finally:
        conn.close()


@app.route("/api/gallery")
@login_required
def api_gallery():
    return jsonify({"images": _GALLERY_IMAGES})


def main() -> None:
    if not SOURCE.exists():
        print(f"Error: {SOURCE} not found.")
        print("Export your ChatGPT history and place the files in exports/")
        sys.exit(1)

    if not DB_PATH.exists():
        print("First run — building search index (may take some time for large exports)…")
        from build_db import build
        build(SOURCE, DB_PATH)

    build_source_index(SOURCE)
    url=f"http://127.0.0.1:{PORT}"
    print(f"ChatGPT History →  {url} (Ctrl-C to quit)")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=PORT, debug=debug, use_reloader=False)


if __name__ == "__main__":
    main()