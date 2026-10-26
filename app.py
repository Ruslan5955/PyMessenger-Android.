import os
import sqlite3
import uuid
import re
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
            username TEXT UNIQUE COLLATE NOCASE,
            phone TEXT UNIQUE NOT NULL,
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
        
        try:
            con.execute("ALTER TABLE users ADD COLUMN phone TEXT UNIQUE")
        except sqlite3.OperationalError:
            pass


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with db() as con:
        return con.execute("SELECT id, username, phone, display_name, bio, avatar, created_at FROM users WHERE id=?", (user_id,)).fetchone()


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped


def allowed(filename, extensions):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in extensions


@app.route("/uploads/avatars/<filename>")
def uploaded_avatar(filename):
    return send_from_directory(AVATAR_DIR, filename)


def avatar_url(filename):
    return url_for("uploaded_avatar", filename=filename) if filename else ""


def chat_summary(con, chat_id, user_id):
    chat = con.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not chat:
        return None
    members = con.execute("""
        SELECT u.id, u.username, u.phone, u.display_name, u.avatar
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
        subtitle = other["phone"] if other else "Личный чат"
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
@login_required
def index():
    user = current_user()
    with db() as con:
        rows = con.execute("SELECT chat_id FROM chat_members WHERE user_id=?", (user["id"],)).fetchall()
        chats = []
        for r in rows:
            summary = chat_summary(con, r["chat_id"], user["id"])
            if summary is not None:
                chats.append(summary)
        chats.sort(key=lambda x: x["id"], reverse=True)
        
        spotify_playlist = "37i9dQZF1DXcBWIGo3jX2M"
        return render_template("whatsapp.html", user=user, chats=chats, spotify_id=spotify_playlist)


@app.route("/messenger")
@login_required
def messenger():
    return redirect(url_for("index"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "")
        
        clean_phone = re.sub(r"[^\d+]", "", phone)
        
        if len(clean_phone) < 10:
            flash("Укажите корректный номер телефона.", "error")
        elif len(password) < 8:
            flash("Пароль должен содержать минимум 8 символов.", "error")
        elif not display_name or len(display_name) > 40:
            flash("Укажи имя длиной до 40 символов.", "error")
        else:
            try:
                with db() as con:
                    cur = con.execute(
                        "INSERT INTO users(phone,password_hash,display_name,created_at) VALUES(?,?,?,?)",
                        (clean_phone, generate_password_hash(password), display_name, now_iso())
                    )
                    user_id = cur.lastrowid
                    
                    cur = con.execute("INSERT INTO chats(title,is_group,created_by,created_at) VALUES(?,1,?,?)",
                                      ("Избранное", user_id, now_iso()))
                    con.execute("INSERT INTO chat_members(chat_id,user_id,joined_at) VALUES(?,?,?)",
                                (cur.lastrowid, user_id, now_iso()))
                session.clear()
                session["user_id"] = user_id
                flash("Вы успешно зарегистрировались!", "success")
                return redirect(url_for("index"))
            except sqlite3.IntegrityError:
                flash("Этот номер телефона уже зарегистрирован.", "error")
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        
        clean_phone = re.sub(r"[^\d+]", "", phone)
        
        with db() as con:
            user = con.execute("SELECT id, password_hash FROM users WHERE phone=?", (clean_phone,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("index"))
        flash("Неверный номер телефона или пароль.", "error")
    return render_template("auth.html", mode="login")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/upload_avatar", methods=["POST"])
def upload_avatar():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    user = current_user()
    if "avatar" in request.files:
        file = request.files["avatar"]
        if file and allowed(file.filename, ALLOWED_AVATARS):
            ext = file.filename.rsplit(".", 1)[1].lower()
            filename = f"user_{user['id']}_{uuid.uuid4().hex[:8]}.{ext}"
            file.save(str(AVATAR_DIR / filename))
            db().execute("UPDATE users SET avatar = ? WHERE id = ?", (filename, user["id"])).close()
            flash("Аватарка успешно обновлена!", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    # Считываем порт, который дает Render, и открываем доступ для 0.0.0.0
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
