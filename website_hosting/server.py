from flask import Flask, jsonify, request, abort, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, os, secrets, functools, threading, mqtt_receiver, re

app = Flask(__name__, static_folder="static", template_folder="templates")

DB = os.path.join(os.path.dirname(__file__), "./temperature.db")

# token -> infos user
TOKENS = {}


# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(conn, table, column):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


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
            role TEXT NOT NULL DEFAULT 'user',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migration automatique si la table users existait déjà sans role
    if not column_exists(conn, "users", "role"):
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")

    # Crée un compte admin par défaut s'il n'existe pas encore d'admin
    admin = conn.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
    if admin is None:
        admin_hash = generate_password_hash("admin123")
        conn.execute("""
            INSERT OR IGNORE INTO users (username, password_hash, role)
            VALUES (?, ?, ?)
        """, ("admin", admin_hash, "admin"))

    conn.commit()
    conn.close()


def user_exists(username):
    conn = get_db()
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return row is not None


def create_user(username, password, role="user"):
    if role not in ("user", "admin"):
        role = "user"

    password_hash = generate_password_hash(password)

    conn = get_db()
    conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, password_hash, role)
    )
    conn.commit()
    conn.close()


def get_user_by_username(username):
    conn = get_db()
    row = conn.execute(
        "SELECT id, username, password_hash, role FROM users WHERE username = ?",
        (username,)
    ).fetchone()
    conn.close()
    return row


def get_user_by_id(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT id, username, role, created_at FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    return row


# ── Validation ────────────────────────────────────────────────────────────────
def valid_username(username):
    return re.fullmatch(r"[A-Za-z0-9_-]{3,30}", username) is not None


def valid_password(password):
    return len(password) >= 8


def valid_number(value):
    try:
        float(value)
        return True
    except Exception:
        return False


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
        # Une inscription publique crée toujours un simple user
        create_user(username, password, role="user")
        return jsonify({"ok": True, "message": "Compte utilisateur créé avec succès"}), 201
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

    TOKENS[token] = {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"]
    }

    return jsonify({
        "token": token,
        "username": user["username"],
        "role": user["role"]
    })


@app.route("/api/logout", methods=["POST"])
def logout():
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    TOKENS.pop(token, None)
    return jsonify({"ok": True})


def current_user():
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    return TOKENS.get(token)


def require_auth(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            abort(401)
        return fn(*args, **kwargs)
    return wrapper


def require_admin(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            abort(401)
        if user["role"] != "admin":
            return jsonify({"error": "Admin only"}), 403
        return fn(*args, **kwargs)
    return wrapper


@app.route("/api/me")
@require_auth
def me():
    user = current_user()
    return jsonify({
        "id": user["id"],
        "username": user["username"],
        "role": user["role"]
    })


# ── Admin API ─────────────────────────────────────────────────────────────────
@app.route("/api/admin/users")
@require_admin
def admin_users():
    conn = get_db()
    rows = conn.execute("""
        SELECT id, username, role, created_at
        FROM users
        ORDER BY id ASC
    """).fetchall()
    conn.close()

    return jsonify([
        {
            "id": r["id"],
            "username": r["username"],
            "role": r["role"],
            "created_at": r["created_at"]
        }
        for r in rows
    ])


@app.route("/api/admin/users/<int:user_id>/role", methods=["POST"])
@require_admin
def admin_change_role(user_id):
    data = request.get_json(force=True)
    new_role = data.get("role", "").strip()

    if new_role not in ("user", "admin"):
        return jsonify({"error": "Role invalide. Utilise 'user' ou 'admin'."}), 400

    target = get_user_by_id(user_id)
    if target is None:
        return jsonify({"error": "Utilisateur introuvable"}), 404

    requester = current_user()

    # Évite qu'un admin se retire ses propres droits
    if requester["id"] == user_id and new_role != "admin":
        return jsonify({"error": "Tu ne peux pas retirer ton propre rôle admin."}), 400

    conn = get_db()
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "message": "Rôle modifié", "user_id": user_id, "role": new_role})


@app.route("/api/admin/readings/<int:reading_id>", methods=["DELETE"])
@require_admin
def admin_delete_reading(reading_id):
    conn = get_db()
    cur = conn.execute("DELETE FROM temperature_data WHERE id = ?", (reading_id,))
    conn.commit()
    deleted = cur.rowcount
    conn.close()

    if deleted == 0:
        return jsonify({"error": "Mesure introuvable"}), 404

    return jsonify({"ok": True, "message": "Mesure supprimée"})


@app.route("/api/admin/readings", methods=["DELETE"])
@require_admin
def admin_clear_readings():
    conn = get_db()
    conn.execute("DELETE FROM temperature_data")
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "message": "Toutes les mesures ont été supprimées"})


@app.route("/api/admin/readings", methods=["POST"])
@require_admin
def admin_add_reading():
    data = request.get_json(force=True)

    sensor_id = str(data.get("sensor_id", "manual")).strip()
    temperature = data.get("temperature")
    humidity = data.get("humidity")

    if not sensor_id:
        sensor_id = "manual"

    if not valid_number(temperature) or not valid_number(humidity):
        return jsonify({"error": "Température et humidité doivent être des nombres"}), 400

    conn = get_db()
    conn.execute("""
        INSERT INTO temperature_data (sensor_id, temperature, humidity)
        VALUES (?, ?, ?)
    """, (sensor_id, float(temperature), float(humidity)))
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "message": "Mesure ajoutée manuellement"}), 201


@app.route("/api/admin/secret")
@require_admin
def admin_secret():
    return jsonify({
        "message": "Bienvenue admin",
        "flag": "FLAG{admin_access_granted}"
    })


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

    # Évite les erreurs côté frontend si la DB est vide
    result = dict(row)
    result["avg_temp"] = result["avg_temp"] or 0
    result["min_temp"] = result["min_temp"] or 0
    result["max_temp"] = result["max_temp"] or 0
    result["avg_hum"] = result["avg_hum"] or 0
    result["min_hum"] = result["min_hum"] or 0
    result["max_hum"] = result["max_hum"] or 0
    result["first_ts"] = result["first_ts"] or "-"
    result["last_ts"] = result["last_ts"] or "-"

    return jsonify(result)


# ── Frontend ──────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    create_db()
    print("\n  EnviroSense running → http://localhost:6007\n")

    receiver = threading.Thread(target=mqtt_receiver.start, daemon=True)
    receiver.start()

    # Mets 0.0.0.0 si tu veux accéder depuis une autre machine du réseau.
    app.run(host="10.225.203.97", port=6007, debug=True,use_reloader=False)
