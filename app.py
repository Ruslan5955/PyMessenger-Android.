import os
import sqlite3
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
AVATAR_DIR = UPLOAD_DIR / "avatars"
FILE_DIR = UPLOAD_DIR / "files"
DB_PATH = BASE_DIR / "pymessenger.db"

for folder in (AVATAR_DIR, FILE_DIR):
    folder.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("PYMESSENGER_SECRET", "change-this-secret-key-before-public-deployment")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB per upload
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Set COOKIE_SECURE=1 when deployed behind HTTPS.
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"

ALLOWED_AVATARS = {"png", "jpg", "jpeg", "webp", "gif"}
ALLOWED_FILES = {"png", "jpg", "jpeg", "webp", "gif", "pdf", "txt", "zip", "mp3", "wav", "ogg", "m4a", "webm", "mp4", "docx", "xlsx"}
MAX_MESSAGE_LENGTH = 4000


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
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
            FOREIGN KEY(created_by) REFERENCES users(id)
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
        CREATE INDEX IF NOT EXISTS idx_members_user_id ON chat_members(user_id);
        """)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with db() as con:
        return con.execute("SELECT id, username, display_name, bio, avatar, created_at FROM users WHERE id=?", (user_id,)).fetchone()


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped


def allowed(filename, extensions):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in extensions


def avatar_url(filename):
    return url_for("uploaded_avatar", filename=filename) if filename else ""


def chat_summary(con, chat_id, user_id):
    chat = con.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not chat:
        return None
    members = con.execute("""
        SELECT u.id, u.username, u.display_name, u.avatar
        FROM chat_members cm JOIN users u ON u.id=cm.user_id
        WHERE cm.chat_id=? ORDER BY u.display_name COLLATE NOCASE
    """, (chat_id,)).fetchall()
    if not any(m["id"] == user_id for m in members):
        return None
    if chat["is_group"]:
        title = chat["title"]
        avatar = ""
        subtitle = f"Группа · {len(members)} участн."
    else:
        other = next((m for m in members if m["id"] != user_id), None)
        title = other["display_name"] if other else chat["title"]
        avatar = other["avatar"] if other else ""
        subtitle = "@" + other["username"] if other else "Личный чат"
    last = con.execute("""
        SELECT m.body, m.attachment_name, m.created_at, u.display_name
        FROM messages m JOIN users u ON u.id=m.sender_id
        WHERE m.chat_id=? ORDER BY m.id DESC LIMIT 1
    """, (chat_id,)).fetchone()
    preview = ""
    if last:
        preview = last["body"] or ("📎 " + last["attachment_name"] if last["attachment_name"] else "")
        if chat["is_group"]:
            preview = last["display_name"] + ": " + preview
    return {
        "id": chat["id"], "title": title, "avatar": avatar,
        "is_group": bool(chat["is_group"]), "subtitle": subtitle,
        "preview": preview, "created_at": chat["created_at"]
    }


@app.route("/")
def index():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    return redirect(url_for("messenger"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "")
        if not (3 <= len(username) <= 24) or not username.replace("_", "").isalnum():
            flash("Логин должен быть длиной 3–24 символа: буквы, цифры и подчёркивание.", "error")
        elif len(password) < 8:
            flash("Пароль должен содержать минимум 8 символов.", "error")
        elif not display_name or len(display_name) > 40:
            flash("Укажи имя длиной до 40 символов.", "error")
        else:
            try:
                with db() as con:
                    cur = con.execute(
                        "INSERT INTO users(username,password_hash,display_name,created_at) VALUES(?,?,?,?)",
                        (username, generate_password_hash(password), display_name, now_iso())
                    )
                    user_id = cur.lastrowid
                    # Create a personal "Saved messages" chat for the new account.
                    cur = con.execute("INSERT INTO chats(title,is_group,created_by,created_at) VALUES(?,1,?,?)",
                                      ("Избранное", user_id, now_iso()))
                    con.execute("INSERT INTO chat_members(chat_id,user_id,joined_at) VALUES(?,?,?)",
                                (cur.lastrowid, user_id, now_iso()))
                session.clear()
                session["user_id"] = user_id
                flash("Аккаунт создан. Добро пожаловать!", "success")
                return redirect(url_for("messenger"))
            except sqlite3.IntegrityError:
                flash("Такой логин уже занят.", "error")
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with db() as con:
            user = con.execute("SELECT id, password_hash FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("messenger"))
        flash("Неверный логин или пароль.", "error")
    return render_template("auth.html", mode="login")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/messenger")
@login_required
def messenger():
    user = current_user()
    with db() as con:
        rows = con.execute("SELECT chat_id FROM chat_members WHERE user_id=?", (user["id"],)).fetchall()
        chats = [chat_summary(con, r[0], user["id"]) for r in rows if r and r[0] is not None]
        chats.sort(key=lambda x: x["id"], reverse=True)
        selected = request.args.get("chat", type=int)
        if selected not in [c["id"] for c in chats]:
            selected = chats[0]["id"] if chats else None
        return render_template("messenger.html", user=user, chats=chats, selected_chat=selected)


    chats = [c for c in chats if c]
    chats.sort(key=lambda c: c["id"], reverse=True)
    selected = request.args.get("chat", type=int)
    if selected not in [c["id"] for c in chats]:
        selected = chats[0]["id"] if chats else None
    return render_template("messenger.html", user=user, chats=chats, selected_chat=selected)


@app.route("/api/chats")
@login_required
def api_chats():
    user = current_user()
    with db() as con:
        ids = [r["chat_id"] for r in con.execute("SELECT chat_id FROM chat_members WHERE user_id=?", (user["id"],))]
        chats = [chat_summary(con, r["chat_id"], user["id"]) for r in ids]
    chats = [c for c in chats if c]
    chats.sort(key=lambda c: c["id"], reverse=True)
    return jsonify(chats)


@app.route("/api/messages/<int:chat_id>")
@login_required
def api_messages(chat_id):
    user = current_user()
    after = request.args.get("after", default=0, type=int)
    with db() as con:
        member = con.execute("SELECT 1 FROM chat_members WHERE chat_id=? AND user_id=?", (chat_id, user["id"])).fetchone()
        if not member:
            abort(403)
        rows = con.execute("""
            SELECT m.id,m.body,m.attachment,m.attachment_name,m.attachment_kind,m.created_at,
                   u.id AS sender_id,u.username,u.display_name,u.avatar
            FROM messages m JOIN users u ON u.id=m.sender_id
            WHERE m.chat_id=? AND m.id>? ORDER BY m.id ASC LIMIT 200
        """, (chat_id, after)).fetchall()
    return jsonify([{
        "id": r["id"], "body": r["body"], "attachment": r["attachment"],
        "attachment_name": r["attachment_name"], "attachment_kind": r["attachment_kind"],
        "created_at": r["created_at"], "sender_id": r["sender_id"],
        "username": r["username"], "display_name": r["display_name"], "avatar": r["avatar"]
    } for r in rows])


@app.route("/api/messages/<int:chat_id>", methods=["POST"])
@login_required
def send_message(chat_id):
    user = current_user()
    body = request.form.get("body", "").strip()
    upload = request.files.get("file")
    attachment = ""
    attachment_name = ""
    attachment_kind = ""
    if upload and upload.filename:
        original = secure_filename(upload.filename)
        if not original or not allowed(original, ALLOWED_FILES):
            return jsonify({"error": "Этот тип файла не поддерживается."}), 400
        ext = original.rsplit(".", 1)[1].lower()
        stored = f"{uuid.uuid4().hex}.{ext}"
        upload.save(FILE_DIR / stored)
        attachment = stored
        attachment_name = original[:180]
        mime = upload.mimetype or ""
        if mime.startswith("image/"):
            attachment_kind = "image"
        elif mime.startswith("audio/") or ext in {"mp3", "wav", "ogg", "m4a", "webm"}:
            attachment_kind = "audio"
        else:
            attachment_kind = "file"
    if not body and not attachment:
        return jsonify({"error": "Напиши сообщение или прикрепи файл."}), 400
    if len(body) > MAX_MESSAGE_LENGTH:
        return jsonify({"error": f"Сообщение не должно быть длиннее {MAX_MESSAGE_LENGTH} символов."}), 400
    with db() as con:
        member = con.execute("SELECT 1 FROM chat_members WHERE chat_id=? AND user_id=?", (chat_id, user["id"])).fetchone()
        if not member:
            abort(403)
        cur = con.execute("""
            INSERT INTO messages(chat_id,sender_id,body,attachment,attachment_name,attachment_kind,created_at)
            VALUES(?,?,?,?,?,?,?)
        """, (chat_id, user["id"], body, attachment, attachment_name, attachment_kind, now_iso()))
        message_id = cur.lastrowid
    return jsonify({"ok": True, "id": message_id})


@app.route("/api/users/search")
@login_required
def search_users():
    q = request.args.get("q", "").strip()
    user = current_user()
    if len(q) < 2:
        return jsonify([])
    with db() as con:
        rows = con.execute("""
            SELECT id,username,display_name,avatar FROM users
            WHERE id != ? AND (username LIKE ? OR display_name LIKE ?)
            ORDER BY display_name COLLATE NOCASE LIMIT 20
        """, (user["id"], f"%{q}%", f"%{q}%")).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/chats/direct", methods=["POST"])
@login_required
def create_direct():
    user = current_user()
    target_id = request.json.get("user_id") if request.is_json else request.form.get("user_id")
    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Не выбран пользователь."}), 400
    if target_id == user["id"]:
        return jsonify({"error": "Это твой аккаунт."}), 400
    with db() as con:
        target = con.execute("SELECT id FROM users WHERE id=?", (target_id,)).fetchone()
        if not target:
            return jsonify({"error": "Пользователь не найден."}), 404
        existing = con.execute("""
            SELECT c.id FROM chats c
            JOIN chat_members a ON a.chat_id=c.id AND a.user_id=?
            JOIN chat_members b ON b.chat_id=c.id AND b.user_id=?
            WHERE c.is_group=0
              AND (SELECT COUNT(*) FROM chat_members x WHERE x.chat_id=c.id)=2
            LIMIT 1
        """, (user["id"], target_id)).fetchone()
        if existing:
            return jsonify({"chat_id": existing["id"]})
        cur = con.execute("INSERT INTO chats(title,is_group,created_by,created_at) VALUES(?,0,?,?)",
                          ("Личный чат", user["id"], now_iso()))
        cid = cur.lastrowid
        con.executemany("INSERT INTO chat_members(chat_id,user_id,joined_at) VALUES(?,?,?)",
                        [(cid, user["id"], now_iso()), (cid, target_id, now_iso())])
    return jsonify({"chat_id": cid})


@app.route("/api/chats/group", methods=["POST"])
@login_required
def create_group():
    user = current_user()
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    ids = data.get("user_ids", [])
    if not title or len(title) > 60:
        return jsonify({"error": "Название группы должно быть от 1 до 60 символов."}), 400
    if not isinstance(ids, list):
        return jsonify({"error": "Неверный список участников."}), 400
    try:
        ids = list({int(i) for i in ids if int(i) != user["id"]})
    except (TypeError, ValueError):
        return jsonify({"error": "Неверный список участников."}), 400
    ids = ids[:49]
    with db() as con:
        valid = []
        for uid in ids:
            if con.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                valid.append(uid)
        cur = con.execute("INSERT INTO chats(title,is_group,created_by,created_at) VALUES(?,1,?,?)",
                          (title, user["id"], now_iso()))
        cid = cur.lastrowid
        members = [user["id"]] + valid
        con.executemany("INSERT INTO chat_members(chat_id,user_id,joined_at) VALUES(?,?,?)",
                        [(cid, uid, now_iso()) for uid in members])
    return jsonify({"chat_id": cid})


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        bio = request.form.get("bio", "").strip()[:240]
        upload = request.files.get("avatar")
        if not display_name or len(display_name) > 40:
            flash("Имя должно быть от 1 до 40 символов.", "error")
            return redirect(url_for("profile"))
        avatar = user["avatar"]
        if upload and upload.filename:
            original = secure_filename(upload.filename)
            if not allowed(original, ALLOWED_AVATARS):
                flash("Аватарка должна быть PNG, JPG, WEBP или GIF.", "error")
                return redirect(url_for("profile"))
            ext = original.rsplit(".", 1)[1].lower()
            avatar = f"{uuid.uuid4().hex}.{ext}"
            upload.save(AVATAR_DIR / avatar)
        with db() as con:
            con.execute("UPDATE users SET display_name=?, bio=?, avatar=? WHERE id=?",
                        (display_name, bio, avatar, user["id"]))
        flash("Профиль сохранён.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html", user=user)


@app.route("/uploads/avatars/<path:filename>")
def uploaded_avatar(filename):
    return send_from_directory(AVATAR_DIR, filename)


@app.route("/uploads/files/<path:filename>")
@login_required
def uploaded_file(filename):
    user = current_user()
    with db() as con:
        row = con.execute("""
            SELECT 1 FROM messages m JOIN chat_members cm ON cm.chat_id=m.chat_id
            WHERE m.attachment=? AND cm.user_id=? LIMIT 1
        """, (filename, user["id"])).fetchone()
    if not row:
        abort(404)
    return send_from_directory(FILE_DIR, filename, as_attachment=False)


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": "Файл слишком большой. Максимальный размер — 25 МБ."}), 413


@app.errorhandler(403)
def forbidden(_):
    return jsonify({"error": "Нет доступа к этому чату."}), 403


init_db()

if __name__ == "__main__":
    # Local development only. For public deployment use a production WSGI server and HTTPS.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
