
import json
import sqlite3
from paho.mqtt import client as mqtt_client

DB_PATH = "temperature.db"
BROKER = "localhost"
PORT = 1883
TOPIC = "esp8266/dht11"

def insert_data(sensor_id, temperature, humidity):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO temperature_data (sensor_id, temperature, humidity)
        VALUES (?, ?, ?)
    """, (sensor_id, temperature, humidity))

    conn.commit()
    conn.close()

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to the broker MQTT")
        client.subscribe(TOPIC)
    else:
        print("Error :", rc)

def on_message(client, userdata, msg):
    print("Message received :", msg.payload.decode())

    try:
        data = json.loads(msg.payload.decode())

        sensor_id = data.get("sensor_id")
        temperature = data.get("temperature")
        humidity = data.get("humidity")

        if sensor_id and temperature and humidity:
            insert_data(sensor_id, temperature, humidity)
            print("Data saved in the database\n")
        else:
            print("Missing data")

    except Exception as e:
        print("Error :", e)
def start():
    client = mqtt_client.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, PORT, 60)
    client.loop_forever()
