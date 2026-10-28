import os
import re
import sqlite3
import sys
import threading
import uuid
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps
from contextlib import contextmanager

from flask import (
    Flask, abort, flash, jsonify, redirect, render_template, request,
    send_from_directory, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
# Keep writable data outside the temporary PyInstaller bundle on Windows.
if getattr(sys, "frozen", False):
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PyMessenger"
else:
    DATA_DIR = BASE_DIR
INSTANCE_DIR = Path(os.environ.get("PYMESSENGER_INSTANCE", DATA_DIR / "instance"))
UPLOAD_DIR = Path(os.environ.get("PYMESSENGER_UPLOADS", DATA_DIR / "uploads"))
AVATAR_DIR = UPLOAD_DIR / "avatars"
FILE_DIR = UPLOAD_DIR / "files"
for folder in (INSTANCE_DIR, AVATAR_DIR, FILE_DIR):
    folder.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("PYMESSENGER_SECRET") or "dev-only-change-this-secret",
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
)
DB_PATH = Path(os.environ.get("PYMESSENGER_DB", INSTANCE_DIR / "pymessenger.db"))
ALLOWED_AVATAR_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
ALLOWED_FILE_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "webp", "pdf", "txt", "csv", "zip",
    "doc", "docx", "xls", "xlsx", "ppt", "pptx", "mp3", "wav", "ogg",
    "m4a", "webm", "mp4", "mov"
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_db():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 15000")
    try:
        with con:
            yield con
    finally:
        con.close()


def init_db():
    with get_db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            phone TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '🟢 в сети',
            bio TEXT NOT NULL DEFAULT '',
            avatar TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            is_group INTEGER NOT NULL DEFAULT 0,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS chat_members (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            joined_at TEXT NOT NULL,
            PRIMARY KEY(chat_id, user_id),
            FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            attachment TEXT NOT NULL DEFAULT '',
            attachment_name TEXT NOT NULL DEFAULT '',
            attachment_kind TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE,
            FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id, id);
        CREATE TABLE IF NOT EXISTS call_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            recipient_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE,
            FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(recipient_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_call_signals_recipient ON call_signals(recipient_id, id);
        """)


init_db()


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with get_db() as con:
        return con.execute(
            "SELECT id, username, phone, display_name, status, bio, avatar, created_at "
            "FROM users WHERE id=?", (user_id,)
        ).fetchone()


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Нужно войти в аккаунт."}), 401
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped


def chat_member(con, chat_id, user_id):
    return con.execute(
        "SELECT 1 FROM chat_members WHERE chat_id=? AND user_id=?",
        (chat_id, user_id)
    ).fetchone() is not None


def create_saved_chat(con, user_id):
    cur = con.execute(
        "INSERT INTO chats(title,is_group,created_by,created_at) VALUES(?,1,?,?)",
        ("Избранное", user_id, now_iso())
    )
    con.execute(
        "INSERT INTO chat_members(chat_id,user_id,joined_at) VALUES(?,?,?)",
        (cur.lastrowid, user_id, now_iso())
    )


def safe_user_name(display_name, phone):
    name = (display_name or "").strip()[:40]
    return name or phone


def create_user(con, phone, password, display_name):
    username_base = "user_" + re.sub(r"\D", "", phone)[-16:]
    username = username_base or "user"
    suffix = 1
    while con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        username = f"{username_base}_{suffix}"
        suffix += 1
    cur = con.execute(
        "INSERT INTO users(username,phone,password_hash,display_name,created_at) VALUES(?,?,?,?,?)",
        (username, phone, generate_password_hash(password), safe_user_name(display_name, phone), now_iso())
    )
    create_saved_chat(con, cur.lastrowid)
    return cur.lastrowid


def valid_extension(filename, allowed):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed


@app.get("/")
def index():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    return redirect(url_for("messenger"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if session.get("user_id"):
            return redirect(url_for("messenger"))
        return render_template("auth.html", mode="login")

    phone = re.sub(r"\D", "", request.form.get("phone", ""))
    password = request.form.get("password", "")
    display_name = request.form.get("display_name", "")
    if len(phone) < 7 or len(phone) > 18 or not password:
        flash("Введи корректный номер телефона и пароль.", "error")
        return render_template("auth.html", mode="login"), 400

    with get_db() as con:
        user = con.execute("SELECT id,password_hash FROM users WHERE phone=?", (phone,)).fetchone()
        if user:
            if not check_password_hash(user["password_hash"], password):
                flash("Неверный номер или пароль.", "error")
                return render_template("auth.html", mode="login"), 401
            user_id = user["id"]
        else:
            if len(password) < 6:
                flash("Для нового аккаунта пароль должен содержать не менее 6 символов.", "error")
                return render_template("auth.html", mode="login"), 400
            user_id = create_user(con, phone, password, display_name)
    session.clear()
    session["user_id"] = user_id
    return redirect(url_for("messenger"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        return login()
    return redirect(url_for("login"))


@app.post("/logout")
@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/messenger")
@login_required
def messenger():
    user = current_user()
    selected_chat = request.args.get("chat", type=int)
    chats = list_chats(session["user_id"])
    if selected_chat and not any(int(c["id"]) == selected_chat for c in chats):
        selected_chat = None
    selected_direct = False
    if selected_chat:
        with get_db() as con:
            row = con.execute("SELECT is_group FROM chats WHERE id=?", (selected_chat,)).fetchone()
            selected_direct = bool(row and not row["is_group"])
    return render_template(
        "messenger.html", user=user, chats=chats, selected_chat=selected_chat,
        selected_direct=selected_direct
    )


def list_chats(user_id):
    with get_db() as con:
        rows = con.execute("""
            SELECT c.id, c.title, c.is_group, c.created_at,
                   (SELECT m.body FROM messages m WHERE m.chat_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_body,
                   (SELECT m.attachment_name FROM messages m WHERE m.chat_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_file
            FROM chats c JOIN chat_members cm ON cm.chat_id=c.id
            WHERE cm.user_id=?
            ORDER BY COALESCE((SELECT MAX(m.id) FROM messages m WHERE m.chat_id=c.id), 0) DESC, c.id DESC
        """, (user_id,)).fetchall()
        output = []
        for row in rows:
            title = row["title"]
            avatar = ""
            subtitle = "Групповой чат" if row["is_group"] else "Личный чат"
            if not row["is_group"]:
                other = con.execute("""
                    SELECT u.display_name,u.avatar FROM users u
                    JOIN chat_members cm ON cm.user_id=u.id
                    WHERE cm.chat_id=? AND u.id<>? LIMIT 1
                """, (row["id"], user_id)).fetchone()
                if other:
                    title, avatar = other["display_name"], other["avatar"]
                    subtitle = "Личный чат"
            preview = row["last_body"] or (row["last_file"] or "")
            output.append({
                "id": row["id"], "title": title, "avatar": avatar,
                "is_group": row["is_group"], "subtitle": subtitle,
                "preview": preview, "created_at": row["created_at"]
            })
        return output


@app.get("/profile")
@login_required
def profile():
    return render_template("profile.html", user=current_user())


@app.post("/profile")
@login_required
def update_profile():
    display_name = (request.form.get("display_name") or "").strip()[:40]
    bio = (request.form.get("bio") or "").strip()[:240]
    if not display_name:
        flash("Имя не может быть пустым.", "error")
        return redirect(url_for("profile"))
    avatar_name = None
    upload = request.files.get("avatar")
    if upload and upload.filename:
        if not valid_extension(upload.filename, ALLOWED_AVATAR_EXTENSIONS):
            flash("Формат аватарки должен быть PNG, JPG, WEBP или GIF.", "error")
            return redirect(url_for("profile"))
        avatar_name = f"{uuid.uuid4().hex}.{secure_filename(upload.filename).rsplit('.',1)[1].lower()}"
        upload.save(AVATAR_DIR / avatar_name)
    with get_db() as con:
        if avatar_name:
            con.execute("UPDATE users SET display_name=?,bio=?,avatar=? WHERE id=?",
                        (display_name, bio, avatar_name, session["user_id"]))
        else:
            con.execute("UPDATE users SET display_name=?,bio=? WHERE id=?",
                        (display_name, bio, session["user_id"]))
    flash("Профиль сохранён.", "success")
    return redirect(url_for("profile"))


@app.get("/uploads/avatars/<path:filename>")
@login_required
def uploaded_avatar(filename):
    return send_from_directory(AVATAR_DIR, filename, as_attachment=False)


@app.get("/uploads/files/<path:filename>")
@login_required
def uploaded_file(filename):
    with get_db() as con:
        row = con.execute("""
            SELECT 1 FROM messages m JOIN chat_members cm ON cm.chat_id=m.chat_id
            WHERE m.attachment=? AND cm.user_id=? LIMIT 1
        """, (filename, session["user_id"])).fetchone()
    if not row:
        abort(404)
    return send_from_directory(FILE_DIR, filename, as_attachment=False)


@app.get("/api/chats")
@login_required
def api_chats():
    return jsonify(list_chats(session["user_id"]))


@app.post("/api/chats/direct")
@login_required
def create_direct_chat():
    data = request.get_json(silent=True) or {}
    try:
        other_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Пользователь не выбран."}), 400
    me = session["user_id"]
    if other_id == me:
        return jsonify({"error": "Нельзя создать чат с самим собой."}), 400
    with get_db() as con:
        if not con.execute("SELECT 1 FROM users WHERE id=?", (other_id,)).fetchone():
            return jsonify({"error": "Пользователь не найден."}), 404
        existing = con.execute("""
            SELECT c.id FROM chats c
            JOIN chat_members a ON a.chat_id=c.id AND a.user_id=?
            JOIN chat_members b ON b.chat_id=c.id AND b.user_id=?
            WHERE c.is_group=0 LIMIT 1
        """, (me, other_id)).fetchone()
        if existing:
            return jsonify({"chat_id": existing["id"]})
        cur = con.execute(
            "INSERT INTO chats(title,is_group,created_by,created_at) VALUES(?,0,?,?)",
            ("Личный чат", me, now_iso())
        )
        chat_id = cur.lastrowid
        con.executemany(
            "INSERT INTO chat_members(chat_id,user_id,joined_at) VALUES(?,?,?)",
            [(chat_id, me, now_iso()), (chat_id, other_id, now_iso())]
        )
    return jsonify({"chat_id": chat_id}), 201


@app.post("/api/chats/group")
@login_required
def create_group_chat():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()[:60]
    if not title:
        return jsonify({"error": "Введи название группы."}), 400
    try:
        member_ids = {int(x) for x in data.get("user_ids", [])}
    except (TypeError, ValueError):
        return jsonify({"error": "Некорректный список участников."}), 400
    member_ids.discard(session["user_id"])
    with get_db() as con:
        valid = set(r["id"] for r in con.execute(
            "SELECT id FROM users WHERE id IN (%s)" % (",".join("?" for _ in member_ids) or "NULL"),
            tuple(member_ids)
        ).fetchall())
        cur = con.execute(
            "INSERT INTO chats(title,is_group,created_by,created_at) VALUES(?,1,?,?)",
            (title, session["user_id"], now_iso())
        )
        chat_id = cur.lastrowid
        con.executemany(
            "INSERT INTO chat_members(chat_id,user_id,joined_at) VALUES(?,?,?)",
            [(chat_id, session["user_id"], now_iso())] +
            [(chat_id, uid, now_iso()) for uid in sorted(valid)]
        )
    return jsonify({"chat_id": chat_id}), 201


@app.get("/api/users/search")
@login_required
def search_users():
    q = (request.args.get("q") or "").strip()[:80]
    if len(q) < 2:
        return jsonify([])
    pattern = f"%{q}%"
    with get_db() as con:
        rows = con.execute("""
            SELECT id,username,display_name,avatar FROM users
            WHERE id<>? AND (username LIKE ? OR display_name LIKE ?)
            ORDER BY display_name LIMIT 30
        """, (session["user_id"], pattern, pattern)).fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/api/messages/<int:chat_id>", methods=["GET", "POST"])
@login_required
def api_messages(chat_id):
    user_id = session["user_id"]
    with get_db() as con:
        if not chat_member(con, chat_id, user_id):
            return jsonify({"error": "Чат не найден."}), 404
        if request.method == "GET":
            after = request.args.get("after", default=0, type=int)
            rows = con.execute("""
                SELECT m.id,m.chat_id,m.sender_id,m.body,m.attachment,m.attachment_name,
                       m.attachment_kind,m.created_at,u.username,u.display_name,u.avatar
                FROM messages m JOIN users u ON u.id=m.sender_id
                WHERE m.chat_id=? AND m.id>? ORDER BY m.id ASC LIMIT 200
            """, (chat_id, max(after, 0))).fetchall()
            return jsonify([dict(row) for row in rows])

        body = (request.form.get("body") or "").strip()
        upload = request.files.get("file")
        if len(body) > 4000:
            return jsonify({"error": "Сообщение слишком длинное (максимум 4000 символов)."}), 400
        attachment = attachment_name = attachment_kind = ""
        if upload and upload.filename:
            original = secure_filename(upload.filename) or "attachment"
            if not valid_extension(original, ALLOWED_FILE_EXTENSIONS):
                return jsonify({"error": "Этот тип файла пока не поддерживается."}), 400
            ext = original.rsplit(".", 1)[1].lower()
            attachment = f"{uuid.uuid4().hex}.{ext}"
            attachment_name = original[:180]
            if ext in {"png", "jpg", "jpeg", "gif", "webp"}:
                attachment_kind = "image"
            elif ext in {"mp3", "wav", "ogg", "m4a", "webm"}:
                attachment_kind = "audio"
            else:
                attachment_kind = "file"
            upload.save(FILE_DIR / attachment)
        if not body and not attachment:
            return jsonify({"error": "Нельзя отправить пустое сообщение."}), 400
        cur = con.execute("""
            INSERT INTO messages(chat_id,sender_id,body,attachment,attachment_name,attachment_kind,created_at)
            VALUES(?,?,?,?,?,?,?)
        """, (chat_id, user_id, body, attachment, attachment_name, attachment_kind, now_iso()))
        msg_id = cur.lastrowid
        row = con.execute("""
            SELECT m.id,m.chat_id,m.sender_id,m.body,m.attachment,m.attachment_name,
                   m.attachment_kind,m.created_at,u.username,u.display_name,u.avatar
            FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.id=?
        """, (msg_id,)).fetchone()
    return jsonify(dict(row)), 201


@app.get("/api/calls/config")
@login_required
def calls_config():
    ice_servers = [{"urls": "stun:stun.l.google.com:19302"}]
    # Configure a TURN provider for reliable calls across restrictive NATs.
    turn_url = os.environ.get("PYMESSENGER_TURN_URL", "").strip()
    turn_user = os.environ.get("PYMESSENGER_TURN_USERNAME", "").strip()
    turn_password = os.environ.get("PYMESSENGER_TURN_CREDENTIAL", "").strip()
    if turn_url and turn_user and turn_password:
        ice_servers.append({
            "urls": turn_url,
            "username": turn_user,
            "credential": turn_password,
        })
    return jsonify({"iceServers": ice_servers})


@app.post("/api/calls/<int:chat_id>/signal")
@login_required
def calls_signal(chat_id):
    data = request.get_json(silent=True) or {}
    kind = (data.get("kind") or "").strip()
    allowed = {"offer", "answer", "ice", "hangup", "decline"}
    if kind not in allowed:
        return jsonify({"error": "Неизвестный тип сигнала."}), 400
    payload = data.get("payload") or ""
    if not isinstance(payload, str) or len(payload) > 100_000:
        return jsonify({"error": "Сигнал слишком большой."}), 400
    user_id = session["user_id"]
    with get_db() as con:
        if not chat_member(con, chat_id, user_id):
            return jsonify({"error": "Чат не найден."}), 404
        chat = con.execute("SELECT is_group FROM chats WHERE id=?", (chat_id,)).fetchone()
        if not chat or chat["is_group"]:
            return jsonify({"error": "Звонки доступны только в личных чатах."}), 400
        recipient = con.execute(
            "SELECT user_id FROM chat_members WHERE chat_id=? AND user_id<>? LIMIT 1",
            (chat_id, user_id)
        ).fetchone()
        if not recipient:
            return jsonify({"error": "Собеседник не найден."}), 404
        con.execute("""
            INSERT INTO call_signals(chat_id,sender_id,recipient_id,kind,payload,created_at)
            VALUES(?,?,?,?,?,?)
        """, (chat_id, user_id, recipient["user_id"], kind, payload, now_iso()))
    return jsonify({"success": True})


@app.get("/api/calls/poll")
@login_required
def calls_poll():
    after = request.args.get("after", default=0, type=int)
    with get_db() as con:
        rows = con.execute("""
            SELECT s.id,s.chat_id,s.kind,s.payload,s.created_at,u.display_name AS from_name
            FROM call_signals s JOIN users u ON u.id=s.sender_id
            WHERE s.recipient_id=? AND s.id>? ORDER BY s.id ASC LIMIT 100
        """, (session["user_id"], max(after, 0))).fetchall()
        last_id = max([after] + [r["id"] for r in rows])
        return jsonify({"signals": [dict(row) for row in rows], "last_id": last_id})


@app.errorhandler(413)
def too_large(_error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Файл больше 25 МБ."}), 413
    return "Файл слишком большой. Максимум 25 МБ.", 413


@app.errorhandler(500)
def internal_error(_error):
    app.logger.exception("Unhandled server error")
    if request.path.startswith("/api/"):
        return jsonify({"error": "Внутренняя ошибка сервера. Проверь журнал приложения."}), 500
    return "Внутренняя ошибка сервера. Проверь журнал приложения.", 500


if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(
        host=os.environ.get("PYMESSENGER_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
