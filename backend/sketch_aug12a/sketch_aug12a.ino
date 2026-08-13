#include <WiFi.h>
#include <PubSubClient.h>

const char* WIFI_SSID = "Motivity-CAccess";
const char* WIFI_PASSWORD = "M0t1v1ty#987CA";

// Your PC's Wi-Fi IPv4 address
const char* MQTT_SERVER = "172.22.3.140";

const int MQTT_PORT = 1883;

const char* MQTT_TOPIC = "detection/events";

WiFiClient espClient;
PubSubClient client(espClient);

void setup_wifi() {

  Serial.println();
  Serial.print("Connecting to Wi-Fi: ");
  Serial.println(WIFI_SSID);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("Wi-Fi connected!");

  Serial.print("ESP32 IP address: ");
  Serial.println(WiFi.localIP());
}

void reconnectMQTT() {

  while (!client.connected()) {

    Serial.print("Connecting to MQTT...");

    String clientId = "ESP32-Sentinel-";
    clientId += String(random(0xffff), HEX);

    if (client.connect(clientId.c_str())) {

      Serial.println("connected!");

    } else {

      Serial.print("failed, state=");
      Serial.println(client.state());

      delay(2000);
    }
  }
}

void setup() {

  Serial.begin(115200);

  setup_wifi();

  client.setServer(MQTT_SERVER, MQTT_PORT);
}

void loop() {

  if (!client.connected()) {
    reconnectMQTT();
  }

  client.loop();

  // Test JSON message
  String json = "{";
  json += "\"cameraId\":\"ESP32-CAM-01\",";
  json += "\"event\":\"PERSON_DETECTED\",";
  json += "\"confidence\":0.95,";
  json += "\"timestamp\":\"2026-08-12T18:30:00\"";
  json += "}";

  Serial.println("Publishing message:");
  Serial.println(json);

  client.publish(MQTT_TOPIC, json.c_str());

  delay(5000);
}