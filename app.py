# --- MONKEY PATCH FOR EVENTLET ---
import eventlet
eventlet.monkey_patch()

# --- IMPORTS AFTER MONKEY PATCH ---
import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory, flash
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO, join_room, emit
from datetime import datetime

# --- CONFIG ---
app = Flask(__name__)
app.secret_key = "supersecretkey"
socketio = SocketIO(app, cors_allowed_origins="*")

UPLOAD_FOLDER = "uploads"
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

DB_PATH = "chat.db"

# --- DATABASE HELPERS ---
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            username TEXT,
            description TEXT,
            profile_pic TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_name TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS chat_users (
            chat_id INTEGER,
            user_id INTEGER
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            sender_id INTEGER,
            content TEXT,
            file_path TEXT,
            timestamp TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- ROUTES ---
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        action = request.form.get("action")
        email = request.form.get("email")
        password = request.form.get("password")
        conn = get_db()
        cur = conn.cursor()

        if action == "register":
            try:
                cur.execute("INSERT INTO users (email, password) VALUES (?,?)", (email, password))
                conn.commit()
                flash("Registered successfully! Please login.", "success")
            except sqlite3.IntegrityError:
                flash("Email already registered!", "error")
            return redirect(url_for("login"))

        elif action == "login":
            cur.execute("SELECT * FROM users WHERE email=? AND password=?", (email, password))
            user = cur.fetchone()
            if user:
                session['user_id'] = user['id']
                session['username'] = user['username'] or user['email']
                session['profile_pic'] = user['profile_pic']
                return redirect(url_for("users"))
            else:
                flash("Invalid credentials!", "error")
                return redirect(url_for("login"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/users")
def users():
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id != ?", (session['user_id'],))
    users_list = cur.fetchall()
    return render_template("users.html", users=users_list)


@app.route("/new_chat", methods=["POST"])
def new_chat():
    if "user_id" not in session:
        return redirect(url_for("login"))

    chat_name = request.form.get("chat_name") or None
    selected_user_ids = request.form.getlist("user_ids")
    current_user_id = session['user_id']

    conn = get_db()
    cur = conn.cursor()

    # Create chat
    cur.execute("INSERT INTO chats (chat_name) VALUES (?)", (chat_name,))
    chat_id = cur.lastrowid

    # Add current user + selected users to chat_users
    all_users = selected_user_ids + [str(current_user_id)]
    for uid in all_users:
        cur.execute("INSERT INTO chat_users (chat_id, user_id) VALUES (?,?)", (chat_id, uid))
    conn.commit()

    return redirect(url_for("chat", chat_id=chat_id))


@app.route("/chat/<int:chat_id>")
def chat(chat_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    cur = conn.cursor()

    # Check if current user is part of this chat
    cur.execute("SELECT 1 FROM chat_users WHERE chat_id=? AND user_id=?", (chat_id, session['user_id']))
    if not cur.fetchone():
        flash("You are not allowed to access this chat.", "error")
        return redirect(url_for("users"))

    # Fetch messages
    cur.execute("""
        SELECT messages.*, users.username, users.profile_pic 
        FROM messages 
        LEFT JOIN users ON messages.sender_id = users.id 
        WHERE chat_id=? 
        ORDER BY id ASC
    """, (chat_id,))
    messages = cur.fetchall()

    # Chat name
    cur.execute("SELECT chat_name FROM chats WHERE id=?", (chat_id,))
    chat_row = cur.fetchone()
    chat_name = chat_row['chat_name'] if chat_row else f"Chat {chat_id}"

    # Current user
    cur.execute("SELECT * FROM users WHERE id=?", (session['user_id'],))
    current_user = cur.fetchone()

    return render_template("chat.html", messages=messages, chat_id=chat_id,
                           current_user=current_user, chat_name=chat_name)


@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (session['user_id'],))
    user = cur.fetchone()

    if request.method == "POST":
        username = request.form.get("username")
        description = request.form.get("description")
        file = request.files.get("profile_pic")
        filename = user['profile_pic']

        if file:
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        cur.execute("UPDATE users SET username=?, description=?, profile_pic=? WHERE id=?",
                    (username, description, filename, session['user_id']))
        conn.commit()
        flash("Profile updated successfully!", "success")
        return redirect(url_for("profile"))

    return render_template("profile.html", user=user)


@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route("/upload", methods=["POST"])
def upload():
    if "user_id" not in session:
        return "", 401

    chat_id = request.form.get("chat_id")
    file = request.files.get("file")
    content = request.form.get("content")  # Optional text with file
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sender_id = session['user_id']

    conn = get_db()
    cur = conn.cursor()

    # Save file if exists
    file_path = None
    if file:
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        file_path = filename

    cur.execute("INSERT INTO messages (chat_id, sender_id, content, file_path, timestamp) VALUES (?,?,?,?,?)",
                (chat_id, sender_id, content, file_path, timestamp))
    conn.commit()

    # Emit to room
    cur.execute("SELECT username, profile_pic, email FROM users WHERE id=?", (sender_id,))
    user = cur.fetchone()

    emit('receive_message', {
        "sender": user['username'] if user['username'] else user['email'],
        "profile_pic": user['profile_pic'],
        "content": content,
        "file_path": file_path,
        "timestamp": timestamp
    }, room=chat_id, broadcast=True)

    return "OK"


# --- SOCKET.IO ---
@socketio.on('join')
def on_join(data):
    chat_id = data['chat_id']
    user_id = session['user_id']

    # Check if user is part of the chat
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM chat_users WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    if cur.fetchone():
        join_room(chat_id)


@socketio.on('send_message')
def handle_message(data):
    chat_id = data['chat_id']
    content = data.get('content', '').strip()
    file_path = data.get('file_path', None)
    sender_id = session['user_id']
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not content and not file_path:
        return  # nothing to send

    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO messages (chat_id, sender_id, content, file_path, timestamp) VALUES (?,?,?,?,?)",
                (chat_id, sender_id, content, file_path, timestamp))
    conn.commit()

    cur.execute("SELECT username, profile_pic, email FROM users WHERE id=?", (sender_id,))
    user = cur.fetchone()

    emit('receive_message', {
        "sender": user['username'] if user['username'] else user['email'],
        "profile_pic": user['profile_pic'],
        "content": content,
        "file_path": file_path,
        "timestamp": timestamp
    }, room=chat_id)


# --- RUN APP ---
if __name__ == "__main__":
    socketio.run(app, debug=False)
