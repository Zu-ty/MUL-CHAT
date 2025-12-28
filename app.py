import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, session, url_for, send_from_directory, flash
from flask_socketio import SocketIO, join_room, leave_room, emit
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# -------------------------------
# CONFIGURATION
# -------------------------------

app = Flask(__name__)
app.secret_key = 'your_super_secret_key_here'  # CHANGE THIS
socketio = SocketIO(app)

# Persistent paths (Render-safe)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'database', 'chat.db')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'txt', 'docx'}

os.makedirs(os.path.join(BASE_DIR, 'database'), exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# -------------------------------
# DATABASE UTILITIES
# -------------------------------

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE,
                    password_hash TEXT,
                    username TEXT,
                    description TEXT,
                    profile_pic TEXT
                )''')
    # Chats table
    c.execute('''CREATE TABLE IF NOT EXISTS chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    is_group INTEGER
                )''')
    # Chat members
    c.execute('''CREATE TABLE IF NOT EXISTS chat_members (
                    chat_id INTEGER,
                    user_id INTEGER
                )''')
    # Messages
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    sender_id INTEGER,
                    content TEXT,
                    file_path TEXT,
                    timestamp DATETIME
                )''')
    conn.commit()
    conn.close()

init_db()

# -------------------------------
# AUTHENTICATION ROUTES
# -------------------------------

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        action = request.form.get('action')
        email = request.form['email'].strip()
        password = request.form['password'].strip()
        conn = get_db_connection()
        c = conn.cursor()

        # ---------------------------
        # REGISTER
        # ---------------------------
        if action == 'register':
            existing = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if existing:
                flash('Email already registered!', 'error')
                conn.close()
                return redirect('/')
            password_hash = generate_password_hash(password)
            c.execute("INSERT INTO users (email, password_hash, username, description) VALUES (?, ?, ?, ?)",
                      (email, password_hash, '', ''))
            conn.commit()
            conn.close()
            flash('Registration successful! Please login.', 'success')
            return redirect('/')

        # ---------------------------
        # LOGIN
        # ---------------------------
        elif action == 'login':
            user = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            conn.close()
            if user and check_password_hash(user['password_hash'], password):
                session['user_id'] = user['id']
                session['username'] = user['username'] if user['username'] else email
                flash('Login successful!', 'success')
                return redirect('/users')
            else:
                flash('Invalid credentials', 'error')
                return redirect('/')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# -------------------------------
# USERS LIST / HUB
# -------------------------------

@app.route('/users')
def users():
    if 'user_id' not in session:
        return redirect('/')
    conn = get_db_connection()
    c = conn.cursor()
    all_users = c.execute("SELECT * FROM users WHERE id != ?", (session['user_id'],)).fetchall()
    conn.close()
    return render_template('users.html', users=all_users)

# -------------------------------
# PROFILE EDIT
# -------------------------------

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session:
        return redirect('/')
    conn = get_db_connection()
    c = conn.cursor()
    user = c.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()

    if request.method == 'POST':
        username = request.form['username'].strip()
        description = request.form['description'].strip()
        profile_pic = request.files.get('profile_pic')
        pic_path = user['profile_pic']

        if profile_pic and allowed_file(profile_pic.filename):
            filename = secure_filename(profile_pic.filename)
            profile_pic.save(os.path.join(UPLOAD_FOLDER, filename))
            pic_path = filename

        c.execute("UPDATE users SET username=?, description=?, profile_pic=? WHERE id=?",
                  (username, description, pic_path, session['user_id']))
        conn.commit()
        session['username'] = username if username else session['username']
        flash('Profile updated!', 'success')
        conn.close()
        return redirect('/profile')

    conn.close()
    return render_template('profile.html', user=user)

# -------------------------------
# CHAT ROUTES
# -------------------------------

@app.route('/chat/<int:chat_id>')
def chat(chat_id):
    if 'user_id' not in session:
        return redirect('/')
    conn = get_db_connection()
    c = conn.cursor()
    # Check if user belongs to chat
    membership = c.execute("SELECT * FROM chat_members WHERE chat_id=? AND user_id=?", 
                           (chat_id, session['user_id'])).fetchone()
    if not membership:
        conn.close()
        return "Unauthorized", 403

    # Load messages
    messages = c.execute("SELECT m.*, u.username, u.profile_pic FROM messages m "
                         "JOIN users u ON m.sender_id=u.id "
                         "WHERE m.chat_id=? ORDER BY m.timestamp ASC", (chat_id,)).fetchall()
    # Get chat info
    chat_info = c.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    conn.close()
    return render_template('chat.html', messages=messages, chat_id=chat_id, chat_info=chat_info)

# -------------------------------
# SOCKET.IO EVENTS
# -------------------------------

@socketio.on('join')
def on_join(data):
    room = data['chat_id']
    join_room(room)

@socketio.on('leave')
def on_leave(data):
    room = data['chat_id']
    leave_room(room)

@socketio.on('send_message')
def handle_message(data):
    chat_id = data['chat_id']
    sender_id = session['user_id']
    content = data.get('content', '')
    file_path = None

    # Handle file upload if any
    file_data = data.get('file_name')
    if file_data:
        file_path = secure_filename(file_data)
        # Actual saving happens in Flask upload route (optional)

    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO messages (chat_id, sender_id, content, file_path, timestamp) VALUES (?, ?, ?, ?, ?)",
              (chat_id, sender_id, content, file_path, timestamp))
    conn.commit()
    # Fetch sender info for broadcasting
    user = c.execute("SELECT username, profile_pic FROM users WHERE id=?", (sender_id,)).fetchone()
    conn.close()

    emit('receive_message', {
        'sender': user['username'],
        'profile_pic': user['profile_pic'],
        'content': content,
        'file_path': file_path,
        'timestamp': timestamp
    }, room=str(chat_id))

# -------------------------------
# NEW CHAT / GROUP ROUTES
# -------------------------------

@app.route('/new_chat', methods=['POST'])
def new_chat():
    if 'user_id' not in session:
        return redirect('/')
    user_ids = request.form.getlist('user_ids')  # list of selected user IDs
    is_group = 0
    chat_name = ''
    if len(user_ids) > 1:
        is_group = 1
        chat_name = request.form.get('chat_name', 'New Group Chat')

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO chats (name, is_group) VALUES (?, ?)", (chat_name, is_group))
    chat_id = c.lastrowid
    # Add members
    c.execute("INSERT INTO chat_members (chat_id, user_id) VALUES (?, ?)", (chat_id, session['user_id']))
    for uid in user_ids:
        c.execute("INSERT INTO chat_members (chat_id, user_id) VALUES (?, ?)", (chat_id, uid))
    conn.commit()
    conn.close()
    return redirect(url_for('chat', chat_id=chat_id))

# -------------------------------
# FILE UPLOAD ROUTE
# -------------------------------

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'user_id' not in session:
        return redirect('/')
    file = request.files['file']
    chat_id = request.form['chat_id']
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file.save(os.path.join(UPLOAD_FOLDER, filename))
        # Insert into messages
        conn = get_db_connection()
        c = conn.cursor()
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute("INSERT INTO messages (chat_id, sender_id, content, file_path, timestamp) VALUES (?, ?, ?, ?, ?)",
                  (chat_id, session['user_id'], '', filename, timestamp))
        conn.commit()
        conn.close()
    return redirect(url_for('chat', chat_id=chat_id))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# -------------------------------
# UTILITY
# -------------------------------

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# -------------------------------
# RUN
# -------------------------------

if __name__ == '__main__':
    socketio.run(app, debug=True)