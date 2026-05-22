#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ========== CONFIGURATION ==========
const char* WIFI_SSID = "BAM";
const char* WIFI_PASSWORD = "12345678";
const char* MQTT_SERVER = "192.168.26.155";    // IP de ton PC
const int MQTT_PORT = 1883;
const char* UNIVERSITY = "EMSI";
// ====================================

// Configuration des 3 places
const char* SPOTS[] = {"A1", "A2", "A3"};
const int TRIG_PINS[] = {16, 18, 21};
const int ECHO_PINS[] = {17, 19, 22};

WiFiClient espClient;
PubSubClient client(espClient);
unsigned long lastSend = 0;
bool firstMessageSent = false;
unsigned long startTime = 0;

void setup_wifi() {
  Serial.print("Connexion WiFi...");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\n✅ WiFi connecté !");
  Serial.print("IP ESP32: ");
  Serial.println(WiFi.localIP());
}

void reconnect_mqtt() {
  while (!client.connected()) {
    Serial.print("Connexion MQTT...");
    if (client.connect("ESP32_3_capteurs")) {
      Serial.println(" ✅");
    } else {
      Serial.print(" ❌ erreur: ");
      Serial.println(client.state());
      delay(5000);
    }
  }
}

int lireDistance(int trig, int echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);
  
  long duree = pulseIn(echo, HIGH, 30000);  // Timeout 30ms
  if (duree == 0) return 999;
  int distance = duree * 0.034 / 2;
  return distance;
}

void envoyerMQTT(String spot_id, String status, int battery, int temperature) {
  StaticJsonDocument<256> doc;
  doc["spot_id"] = spot_id;
  doc["university"] = UNIVERSITY;
  doc["status"] = status;
  doc["battery"] = battery;
  doc["temperature"] = temperature;
  doc["confidence"] = 0.95;
  doc["source"] = "esp32_real";
  
  char buffer[256];
  serializeJson(doc, buffer);
  
  client.publish("fastpark/sensors", buffer);
  
  Serial.print("📤 ");
  Serial.print(spot_id);
  Serial.print(" → ");
  Serial.println(status);
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== ESP32 FastPark - 3 capteurs ===");
  
  // Initialisation des broches
  for (int i = 0; i < 3; i++) {
    pinMode(TRIG_PINS[i], OUTPUT);
    pinMode(ECHO_PINS[i], INPUT);
  }
  
  setup_wifi();
  client.setServer(MQTT_SERVER, MQTT_PORT);
  reconnect_mqtt();
  
  startTime = millis();
}

void loop() {
  if (!client.connected()) {
    reconnect_mqtt();
    firstMessageSent = false;
    startTime = millis();
    delay(1000);
    return;
  }
  client.loop();
  
  // Premier envoi : toutes les places à "free"
  if (!firstMessageSent) {
    if (millis() - startTime > 2000) {
      for (int i = 0; i < 3; i++) {
        envoyerMQTT(SPOTS[i], "free", random(70, 100), random(18, 28));
        delay(100);
      }
      firstMessageSent = true;
      lastSend = millis();
      Serial.println("✅ Premier message: free pour A1, A2, A3");
    }
    delay(50);
    return;
  }
  
  // Lecture des 3 capteurs toutes les 3 secondes
  if (millis() - lastSend > 3000) {
    for (int i = 0; i < 3; i++) {
      int distance = lireDistance(TRIG_PINS[i], ECHO_PINS[i]);
      
      // Seuil à 25 cm
      String status = (distance < 25 && distance > 2) ? "occupied" : "free";
      
      // Afficher la distance dans le moniteur série
      Serial.print(SPOTS[i]);
      Serial.print(": ");
      Serial.print(distance);
      Serial.print(" cm → ");
      Serial.println(status);
      
      // Envoyer via MQTT
      envoyerMQTT(SPOTS[i], status, random(70, 100), random(18, 28));
      
      delay(100); // Pause entre chaque capteur
    }
    Serial.println("---");
    lastSend = millis();
  }
  
  delay(50);
}