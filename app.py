from flask import Flask, render_template
import sqlite3

app = Flask(__name__)

DB_PATH = "/root/temperature.db"

def get_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, sensor_id, temperature, humidity, timestamp
        FROM temperature_data
        ORDER BY id DESC
    """)

    rows = cursor.fetchall()
    conn.close()
    return rows

@app.route("/")
def index():
    data = get_data()
    return render_template("index.html", data=data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)