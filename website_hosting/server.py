"""
EnviroSense — minimal sensor dashboard backend
Run: python server.py
Then open: http://localhost:5000
"""
from flask import Flask, jsonify, request, abort, send_from_directory
import sqlite3, os, secrets, functools, threading, mqtt_receiver
app = Flask(__name__, static_folder="static", template_folder="templates")

DB      = os.path.join(os.path.dirname(__file__), "./temperature.db")
USERS   = {"admin": "admin123"}   # change these in production
TOKENS  = set()


# ── Database ──────────────────────────────────────────────────────────────────
def create_db():
    cursor=get_db()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS temperature_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            sensor_id TEXT,
            temperature REAL,
            humidity REAL
        )
    """)
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    user = data.get("username", "")
    pwd  = data.get("password", "")
    if USERS.get(user) == pwd:
        token = secrets.token_hex(16)
        TOKENS.add(token)
        return jsonify({"token": token, "username": user})
    return jsonify({"error": "Invalid credentials"}), 401


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
def readings():
    """Return all readings ordered by timestamp (newest first, up to 1000)."""
    limit = min(int(request.args.get("limit", 300)), 1000)
    conn  = get_db()
    rows  = conn.execute(
        "SELECT id, sensor_id, temperature, humidity, timestamp FROM temperature_data ORDER BY timestamp DESC"
    ).fetchall()
    conn.close()
    data = [{"id":r["id"],"sensor":r["sensor_id"], "ts": r["timestamp"], "temp": r["temperature"], "hum": r["humidity"]}
            for r in rows]
    data.reverse()
    return jsonify(data)


@app.route("/api/stats")
def stats():
    """Return aggregate stats for the summary cards."""
    conn = get_db()
    row  = conn.execute("""
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


# ── Frontend (single-page app) ────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    create_db()
    print("\n  EnviroSense running → http://localhost:5000\n")
    receiver=threading.Thread(target=mqtt_receiver.start)
    receiver.start()
    app.run(host="10.55.242.147", port=6007, debug=True)
