"""
MQTT receiver: listens for sensor data and OTA firmware updates.

OTA attack surface (intentional CTF vulnerability):
  Topic   : esp8266/ota
  Payload : { "name": "v1.3.bin", "data": "<base64>" }
  Bug     : name is joined with FIRMWARE_DIR without sanitisation,
            so "../plugins/pwn.py" writes into the plugin directory.
            Flask debug reloader then re-imports it → RCE.
"""
import json
import os
import base64
import sqlite3
from paho.mqtt import client as mqtt_client

DB_PATH = os.path.join(os.path.dirname(__file__), "temperature.db")
FIRMWARE_DIR = os.path.join(os.path.dirname(__file__), "firmware")

BROKER    = os.environ.get("MQTT_BROKER", "localhost")
PORT      = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "iot_device")
MQTT_PASS = os.environ.get("MQTT_PASS", "iot2024")

DATA_TOPIC   = "esp8266/dht11"
STATUS_TOPIC = "esp8266/status"
OTA_TOPIC    = "esp8266/ota"


def insert_data(sensor_id, temperature, humidity):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO temperature_data (sensor_id, temperature, humidity)
        VALUES (?, ?, ?)
    """, (sensor_id, temperature, humidity))
    conn.commit()
    conn.close()


def handle_sensor_data(payload):
    try:
        data = json.loads(payload)
        sensor_id   = data.get("sensor_id")
        temperature = data.get("temperature")
        humidity    = data.get("humidity")

        if sensor_id is not None and temperature is not None and humidity is not None:
            insert_data(sensor_id, temperature, humidity)
            print(f"[data] sensor={sensor_id} t={temperature} h={humidity}")
        else:
            print("[data] missing fields")
    except Exception as e:
        print(f"[data] error: {e}")


def handle_ota(payload):
    """
    Receive a firmware blob and store it in firmware/ so that sensors
    can download it on next reboot.  Payload format:
      { "name": "v1.3.bin", "data": "<base64>" }

    Vulnerability: name is not sanitised — "../plugins/evil.py" is valid.
    """
    try:
        msg  = json.loads(payload)
        name = msg["name"]
        blob = base64.b64decode(msg["data"])

        os.makedirs(FIRMWARE_DIR, exist_ok=True)
        path = os.path.join(FIRMWARE_DIR, name)   # no sanitisation — intentional

        with open(path, "wb") as f:
            f.write(blob)

        print(f"[ota] firmware stored: {name} ({len(blob)} bytes)")
    except Exception as e:
        print(f"[ota] error: {e}")


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[mqtt] connected to broker")
        client.subscribe(DATA_TOPIC)
        client.subscribe(STATUS_TOPIC + "/#")
        client.subscribe(OTA_TOPIC)
    else:
        print(f"[mqtt] connection failed: rc={rc}")


def on_message(client, userdata, msg):
    topic   = msg.topic
    payload = msg.payload.decode("utf-8", errors="replace")

    if topic == DATA_TOPIC:
        handle_sensor_data(payload)
    elif topic.startswith(STATUS_TOPIC):
        print(f"[status] {topic}: {payload}")
    elif topic == OTA_TOPIC:
        handle_ota(payload)
    else:
        print(f"[mqtt] unhandled topic {topic}")


def start():
    client = mqtt_client.Client()
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(BROKER, PORT, 60)
    except Exception as e:
        print(f"[mqtt] could not connect to {BROKER}:{PORT} -> {e}")
        return

    client.loop_forever()


if __name__ == "__main__":
    start()
