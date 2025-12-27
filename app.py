from flask import Flask, render_template, request, redirect, session, url_for, send_from_directory
from flask_socketio import SocketIO, emit
import sqlite3
from datetime import datetime
import os

app = Flask(__name__)
app.secret_key = "supersecretkey"
socketio = SocketIO(app)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# ---------- DATABASE ----------
def db():
    return sqlite3.connect("chat.db", check_same_thread=False)

conn = db()
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE,
    username TEXT,
    description TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    message TEXT,
    file TEXT,
    timestamp TEXT
)
""")

conn.commit()

# ---------- ROUTES ----------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        username = request.form["username"]
        description = request.form["description"]

        cur.execute("SELECT * FROM users WHERE email=?", (email,))
        user = cur.fetchone()

        if not user:
            cur.execute("INSERT INTO users (email, username, description) VALUES (?, ?, ?)",
                        (email, username, description))
            conn.commit()

        session["user"] = username
        return redirect("/chat")

    return render_template("login.html")


@app.route("/chat")
def chat():
    if "user" not in session:
        return redirect("/")

    cur.execute("SELECT username, message, file, timestamp FROM messages")
    messages = cur.fetchall()

    return render_template("chat.html", user=session["user"], messages=messages)


@app.route("/profile/<username>")
def profile(username):
    cur.execute("SELECT username, description FROM users WHERE username=?", (username,))
    user = cur.fetchone()
    return render_template("profile.html", user=user)


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return redirect("/chat")

    file = request.files["file"]
    if file.filename == "":
        return redirect("/chat")

    filepath = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
    file.save(filepath)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    cur.execute("INSERT INTO messages VALUES (NULL, ?, ?, ?, ?)",
                (session["user"], "[FILE]", file.filename, timestamp))
    conn.commit()

    socketio.emit("receive_message", {
        "user": session["user"],
        "message": "[FILE]",
        "file": file.filename,
        "timestamp": timestamp
    })

    return redirect("/chat")


@app.route("/uploads/<filename>")
def download(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ---------- SOCKET.IO ----------
@socketio.on("send_message")
def handle_message(data):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    cur.execute("INSERT INTO messages VALUES (NULL, ?, ?, ?, ?)",
                (data["user"], data["message"], None, timestamp))
    conn.commit()

    emit("receive_message", {
        "user": data["user"],
        "message": data["message"],
        "timestamp": timestamp
    }, broadcast=True)


if __name__ == "__main__":
    socketio.run(app, debug=False)
