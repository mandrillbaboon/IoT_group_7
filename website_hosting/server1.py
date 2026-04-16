from flask import Flask, jsonify, request, abort, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, os, secrets, functools, threading, mqtt_receiver, re

app = Flask(__name__, static_folder="static", template_folder="templates")

DB = os.path.join(os.path.dirname(__file__), "./temperature.db")
TOKENS = set()


# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def create_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS temperature_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            sensor_id TEXT,
            temperature REAL,
            humidity REAL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def user_exists(username):
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        (username,)
    ).fetchone()
    conn.close()
    return row is not None


def create_user(username, password):
    password_hash = generate_password_hash(password)

    conn = get_db()
    conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, password_hash)
    )
    conn.commit()
    conn.close()


def get_user_by_username(username):
    conn = get_db()
    row = conn.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (username,)
    ).fetchone()
    conn.close()
    return row


# ── Validation ────────────────────────────────────────────────────────────────
def valid_username(username):
    # 3 à 30 caractères, lettres/chiffres/_/-
    return re.fullmatch(r"[A-Za-z0-9_-]{3,30}", username) is not None


def valid_password(password):
    # minimum simple mais correct pour commencer
    return len(password) >= 8


# ── Auth ──────────────────────────────────────────────────────────────────────
@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(force=True)

    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not valid_username(username):
        return jsonify({
            "error": "Username invalide (3-30 caractères, lettres/chiffres/_/- uniquement)"
        }), 400

    if not valid_password(password):
        return jsonify({
            "error": "Le mot de passe doit contenir au moins 8 caractères"
        }), 400

    if user_exists(username):
        return jsonify({"error": "Username déjà utilisé"}), 409

    try:
        create_user(username, password)
        return jsonify({"ok": True, "message": "Compte créé avec succès"}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "Username déjà utilisé"}), 409


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    username = data.get("username", "").strip()
    password = data.get("password", "")

    user = get_user_by_username(username)

    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid credentials"}), 401

    token = secrets.token_hex(32)
    TOKENS.add(token)

    return jsonify({
        "token": token,
        "username": user["username"]
    })


@app.route("/api/logout", methods=["POST"])
def logout():
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    TOKENS.discard(token)
    return jsonify({"ok": True})


def require_auth(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
        if token not in TOKENS:
            abort(401)
        return fn(*args, **kwargs)
    return wrapper


# ── Data API ──────────────────────────────────────────────────────────────────
@app.route("/api/readings")
@require_auth
def readings():
    limit = min(int(request.args.get("limit", 300)), 1000)
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, sensor_id, temperature, humidity, timestamp
        FROM temperature_data
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (limit,)
    ).fetchall()
    conn.close()

    data = [
        {
            "id": r["id"],
            "sensor": r["sensor_id"],
            "ts": r["timestamp"],
            "temp": r["temperature"],
            "hum": r["humidity"]
        }
        for r in rows
    ]
    data.reverse()
    return jsonify(data)


@app.route("/api/stats")
@require_auth
def stats():
    conn = get_db()
    row = conn.execute("""
        SELECT
          COUNT(*)                   AS count,
          ROUND(AVG(temperature), 1) AS avg_temp,
          ROUND(MIN(temperature), 1) AS min_temp,
          ROUND(MAX(temperature), 1) AS max_temp,
          ROUND(AVG(humidity),    1) AS avg_hum,
          ROUND(MIN(humidity),    1) AS min_hum,
          ROUND(MAX(humidity),    1) AS max_hum,
          MIN(timestamp)             AS first_ts,
          MAX(timestamp)             AS last_ts
        FROM temperature_data
    """).fetchone()
    conn.close()

    return jsonify(dict(row))


# ── Frontend ──────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    create_db()
    print("\n  EnviroSense running → http://localhost:5000\n")
    receiver = threading.Thread(target=mqtt_receiver.start, daemon=True)
    receiver.start()
    app.run(host="192.168.0.101", port=6007, debug=True)
