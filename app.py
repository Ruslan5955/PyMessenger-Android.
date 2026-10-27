import os
import sqlite3
import uuid
import re
import random
import requests
import smtplib
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
AVATAR_DIR = UPLOAD_DIR / "avatars"
FILE_DIR = UPLOAD_DIR / "files"

for folder in (AVATAR_DIR, FILE_DIR):
    folder.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("PYMESSENGER_SECRET", "secure-lion-crypto-key-9999")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

_MEM_CONN = sqlite3.connect(":memory:", check_same_thread=False)
_MEM_CONN.row_factory = sqlite3.Row
SMS_CODES = {}

def db():
    return _MEM_CONN

def init_db():
    with db() as con:
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE COLLATE NOCASE,
            phone TEXT UNIQUE NOT NULL,
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
        CREATE TABLE IF NOT EXISTS crypto_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            encrypted_body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE,
            FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """)

def send_free_code_methods_2_and_3(phone, code):
    email = os.environ.get("EMAIL_USER", "your_project_email@gmail.com")
    password = os.environ.get("EMAIL_PASS", "your_email_app_password")
    gateways = [f"{phone}@sms.tele2.ru", f"{phone}@sms.beemail.ru", f"{phone}@sms.mts.ru", f"{phone}@sms.megafon.ru"]
    if email and password:
        try:
            server = smtplib.SMTP("://gmail.com", 587)
            server.starttls()
            server.login(email, password)
            for gate in gateways:
                server.sendmail(email, gate, f"Life and Lion Code: {code}")
            server.quit()
        except Exception: pass

@app.route("/api/request_code", methods=["POST"])
def request_code():
    phone = request.form.get("phone", "").strip()
    clean_phone = re.sub(r"[^\d]", "", phone)
    if len(clean_phone) < 10:
        return jsonify({"success": False, "error": "Неверный формат номера"}), 400
    code = str(random.randint(1000, 9999))
    SMS_CODES[clean_phone] = code
    send_free_code_methods_2_and_3(clean_phone, code)
    return jsonify({"success": True, "code_simulated": code, "flash_call_digits": code})

@app.route("/api/verify_code", methods=["POST"])
def verify_code():
    phone = request.form.get("phone", "").strip()
    code = request.form.get("code", "").strip()
    display_name = request.form.get("display_name", "Лев").strip()
    clean_phone = re.sub(r"[^\d]", "", phone)
    
    if clean_phone in SMS_CODES and SMS_CODES[clean_phone] == code:
        con = db()
        user = con.execute("SELECT id FROM users WHERE phone=?", (clean_phone,)).fetchone()
        if not user:
            fake_pass = generate_password_hash(uuid.uuid4().hex)
            cur = con.execute("INSERT INTO users(phone, password_hash, display_name, created_at) VALUES(?,?,?,?)",
                               (clean_phone, fake_pass, display_name, datetime.now(timezone.utc).isoformat()))
            user_id = cur.lastrowid
            c_cur = con.execute("INSERT INTO chats(title,is_group,created_by,created_at) VALUES(?,1,?,?)",
                                ("Избранное", user_id, datetime.now(timezone.utc).isoformat()))
            con.execute("INSERT INTO chat_members(chat_id,user_id,joined_at) VALUES(?,?,?)",
                        (c_cur.lastrowid, user_id, datetime.now(timezone.utc).isoformat()))
            con.commit()
            user_id = user_id
        else:
            user_id = user["id"]
        session["user_id"] = user_id
        del SMS_CODES[clean_phone]
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Неверный код"}), 400

# ПРИКОЛЮХА: Роут для моментального обновления статуса в профиле
@app.route("/api/update_status", methods=["POST"])
def update_status():
    if not session.get("user_id"): return jsonify({"success": False}), 401
    new_status = request.form.get("status", "🟢 в сети").strip()
    db().execute("UPDATE users SET status = ? WHERE id = ?", (new_status, session["user_id"]))
    db().commit()
    return jsonify({"success": True})

# СЕКРЕТНЫЙ РОУТ: Прием зашифрованных сквозным шифрованием (E2EE) сообщений
@app.route("/api/send_secure", methods=["POST"])
def send_secure():
    if not session.get("user_id"): return jsonify({"success": False}), 401
    chat_id = request.form.get("chat_id")
    encrypted_body = request.form.get("encrypted_body") # Сервер видит только хэш-строку
    if chat_id and encrypted_body:
        db().execute("INSERT INTO crypto_messages(chat_id, sender_id, encrypted_body, created_at) VALUES(?,?,?,?)",
                     (chat_id, session["user_id"], encrypted_body, datetime.now(timezone.utc).isoformat()))
        db().commit()
        return jsonify({"success": True})
    return jsonify({"success": False}), 400

@app.route("/")
def index():
    if not session.get("user_id"): return redirect(url_for("login"))
    user = db().execute("SELECT id, username, phone, display_name, status, bio, avatar, created_at FROM users WHERE id=?", (session["user_id"],)).fetchone()
    chats = []
    spotify_playlist = "37i9dQZF1DXcBWIGo3jX2M"
    return render_template("whatsapp.html", user=user, chats=chats, spotify_id=spotify_playlist)

@app.route("/login")
def login():
    return render_template("auth.html", mode="login")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
