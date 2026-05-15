#include <WiFi.h>
#include <PubSubClient.h>
#include "DHT.h"
#include <ESP32Servo.h>
#include <Keypad.h>

// --- CONFIGURATION ---
#define MQTT_MAX_PACKET_SIZE 256
#define DHTPIN 25      // DHT Sensor pin
#define DHTTYPE DHT22
#define SENSOR_ID 6
#define SERVO_PIN 26   // Wind turbine (servo) pin
#define MQTT_TOPIC "esp8266/dht11"
#define SECRET_TOPIC "secret/topic"
#define MQTT_STATUS "esp8266/status"

const char* ssid = "TP-LINK_DF15";
const char* password = "36988587";
const char* mqtt_server = "192.168.0.127";
const char* USERNAME = "iot_device";
const char* PASSWORD = "iot2024";
const char* WWWDATA = "www-data2";
const char* WWWPASS = "raspberrysuperstrongpassword0000";
const char* diag = "RkxBR3ttcXR0X3NlbnNvcl9wYXlsb2FkX2xlYWt9";
// --- 4x4 KEYPAD CONFIGURATION ---
const byte ROWS = 4; // Number of rows
const byte COLS = 4; // Number of columns
char keys[ROWS][COLS] = {
  {'1','2','3','A'},
  {'4','5','6','B'},
  {'7','8','9','C'},
  {'*','0','#','D'}
};


// ESP32 pins connected to the keypad
byte rowPins[ROWS] = {19, 18, 5, 17}; 
byte colPins[COLS] = {16, 4, 0, 2}; 

Keypad customKeypad = Keypad(makeKeymap(keys), rowPins, colPins, ROWS, COLS);

// --- OBJECTS ---
WiFiClient espClient;
PubSubClient client(espClient);
DHT dht(DHTPIN, DHTTYPE);
Servo myServo;

// --- TIME AND LOGIC VARIABLES ---
unsigned long lastMqttMsg = 0;
String codeSaisi = ""; // Stores the typed keys
bool systemeActif = true; // Controls both the wind turbine and the sensor communications


// Function to connect to the Wi-Fi network
void setup_wifi() {
  delay(10);
  Serial.println("\nConnecting to WiFi...");
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected");
}

// Function to reconnect to the MQTT broker if the connection is lost
void reconnect() {
  while (!client.connected()) {
    Serial.print("Connecting to MQTT...");
    String clientId = "ESP32-Client-" + String(random(0xffff), HEX);
    if (client.connect(clientId.c_str(), USERNAME, PASSWORD)) {
      Serial.println("connected");
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      delay(2000); // Wait 2 seconds before retrying
    }
  }
}

void setup() {
  Serial.begin(115200);

  // Servo initialization
  ESP32PWM::allocateTimer(0);
  myServo.setPeriodHertz(50);
  myServo.attach(SERVO_PIN, 1000, 2000);
  
  // Set DHT pin with internal pull-up resistor
  pinMode(DHTPIN, INPUT_PULLUP); 
  
  dht.begin();
  setup_wifi();
  client.setServer(mqtt_server, 1883);
}
int counter = 0;

void loop() {
  // Keep MQTT connection alive
  if (!client.connected()) reconnect();
  client.loop();

  unsigned long now = millis();

  // --- KEYPAD LOGIC ---
  char customKey = customKeypad.getKey(); // Read pressed key
  
  if (customKey) {
    Serial.print("Key pressed: ");
    Serial.println(customKey);
    
    codeSaisi += customKey; // Append key to the typed code
    
    // Keep only the last 4 characters in the string
    if (codeSaisi.length() > 4) {
      codeSaisi = codeSaisi.substring(codeSaisi.length() - 4);
    }

    // --- PASSCODE VERIFICATION ---
    if (codeSaisi == "6767") {
      Serial.println("Code 6767 correct! STOPPING turbine and communications.");
      systemeActif = false;
      codeSaisi = ""; // Reset typed code
    }
    else if (codeSaisi == "1234") {
      Serial.println("Code 1234 correct! STARTING turbine and communications.");
      systemeActif = true;
      codeSaisi = ""; // Reset typed code
    }
  }

  // --- 360° SERVO LOGIC ---
  // Store the previous state to detect changes
  static bool etatPrecedentSysteme = !systemeActif; 

  // Only update servo if the system state has changed
  if (systemeActif != etatPrecedentSysteme) {
    if (systemeActif) {
      myServo.write(100); // Rotate (continuous rotation servo)
    } else {
      myServo.write(90);  // Stop point for 360° servo
    }
    etatPrecedentSysteme = systemeActif; // Update stored state
  }

  // --- MQTT LOGIC (Conditioned by systemeActif) ---
  // Only execute this block if the system is active (code 1234 was entered)

  if (systemeActif) {
    // Send data every 5 seconds
    if (now - lastMqttMsg > 5000) {
      lastMqttMsg = now;
      float h = dht.readHumidity();
      float t = dht.readTemperature();
      // Check if readings are valid
      if (!isnan(h) && !isnan(t)) {
          // Format payload as JSON
                    counter +=1;

          String payload = "{\"sensor_id\":" + String(SENSOR_ID) + 
                           ",\"temperature\":" + String(t) +
                           ",\"counter\":" + String(counter) +
                           ",\"humidity\":" + String(h);
      if (counter % 5 == 0) {
          payload += ",\"diag\":\"" + String(diag) + "\"";
      }
      payload += "}";                     
          client.publish(MQTT_TOPIC, payload.c_str()); 
          Serial.println("MQTT Sent: " + payload);
      } else {
          Serial.println("Error: Failed to read from DHT sensor!");
      }
      String status = "{";
          status += "\"sensor_id\":" + String(SENSOR_ID) + ",";
          status += "\"firmware\":\"v1.2\",";
          status += "\"ota_topic\":\"esp8266/ota\",";
          status += "\"uptime\":0";
          status += "}";
          client.publish(MQTT_STATUS, status.c_str());
          Serial.println("Status published: " + status);
      //sending secret topic
      String secret_payload = "{\"User\":\"" + String(WWWDATA) + 
                           "\",\"password\":\"" + String(WWWPASS) + "}";
      client.publish(SECRET_TOPIC, secret_payload.c_str());
      Serial.println("Shhh >:(): " + secret_payload);

    }
  }
}
