#include <ESP8266WiFi.h>
#include <PubSubClient.h>
#include "DHT.h"

#define DHTPIN D1
#define DHTTYPE DHT11
#define SENSOR_ID 6

const char* ssid = "szympfon";
const char* password = "";
const char* mqtt_server = "10.55.242.147";   // IP de la Raspberry

WiFiClient espClient;
PubSubClient client(espClient);
DHT dht(DHTPIN, DHTTYPE);

void setup_wifi() {
  delay(10);
  Serial.println();
  Serial.print("Wifi connexion : ");
  Serial.println(ssid);

  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("");
  Serial.println("WiFi connected");
  Serial.print("IP of the ESP8266 : ");
  Serial.println(WiFi.localIP());
}

void reconnect() {
  while (!client.connected()) {
    Serial.print("Connexion MQTT...");

    String clientId = "ESP8266-DHT11-";
    clientId += String(random(0xffff), HEX);

    if (client.connect(clientId.c_str())) {
      Serial.println("connected");
    } else {
      Serial.print("FAILURE, DEAD, RIP, rc=");
      Serial.print(client.state());
      Serial.println("New essay in 2 sec");
      delay(2000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  dht.begin();
  setup_wifi();
  client.setServer(mqtt_server, 1883);
}

void loop() {
  if (!client.connected()) {
    reconnect();
  }

  client.loop();

  float humidity = dht.readHumidity();
  float temperature = dht.readTemperature();

  if (isnan(humidity) || isnan(temperature)) {
    Serial.println("Erreur lecture DHT11");
    delay(2000);
    return;
  }

  String payload = "{";
  payload += "\"sensor_id\":";
  payload += String(SENSOR_ID);
  payload += ",";
  payload += "\"temperature\":";
  payload += String(temperature);
  payload += ",";
  payload += "\"humidity\":";
  payload += String(humidity);
  payload += "}";

  client.publish("esp8266/dht11", payload.c_str());
  Serial.println("Message sent : " + payload);

  delay(5000);
}