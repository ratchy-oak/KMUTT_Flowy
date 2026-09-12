/*
 * ========================================
 * FLOWEY COMPLETE SYSTEM - ARDUINO R4 WIFI
 * ========================================
 * - อ่านค่า Sensor (DHT22, LDR, Soil)
 * - ส่งข้อมูลไปยัง Raspberry Pi (Serial/USB)
 * - ส่งข้อมูลไปยัง MQTT (Flowey)
 * - ส่งข้อมูลไปยัง Plant Chat API
 * - ควบคุมปั๊มน้ำผ่าน MQTT Command
 * ========================================
 */

#include <WiFiS3.h>
#include <ArduinoHttpClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include "FloweyMqtt.h"

// ==================== WiFi Configuration ====================
const char* ssid = "Ratchy Oak 😊 - iPhone";
const char* password = "123456()";

// ==================== Device Configuration ====================
const char* DEVICE_TOKEN = "684605be-5f64-4b3d-9b6b-129ad6727824:eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJvd25lcl9pZCI6NiwicGxhbnR1dWlkIjoiNjg0NjA1YmUtNWY2NC00YjNkLTliNmItMTI5YWQ2NzI3ODI0IiwiaWF0IjoxNzY1NzI5OTMzfQ.9-jnjL41011akpnB0CptunnbHsE6Y_cj1z-xCxLKzQk";

// ==================== Plant Information ====================
String PLANT_NICKNAME = "Flowy";
String PLANT_SPECIES = "Unknown";
String PLANT_AGE = "Unknown";
String USER_NICKNAME = "Friend";

// ==================== Pin Definitions ====================
#define DHTPIN 2        // DHT22 data pin
#define DHTTYPE DHT22   // DHT sensor type
#define LDR_PIN A0      // LDR (Light Dependent Resistor)
#define SOIL_PIN A1     // Soil moisture sensor
#define PUMP_PIN 5      // Pump control pin

// ==================== Sensor Objects ====================
DHT dht(DHTPIN, DHTTYPE);

// ==================== MQTT Objects ====================
WiFiClient espClient;
FloweyMqtt flowey(espClient);

// ==================== Sensor Data Storage ====================
float latestTemp = 0.0;
float latestHumidity = 0.0;
float latestLux = 0.0;    // ค่าแสงในหน่วย Lux เท่านั้น
int latestSoil = 0;       // ค่า Soil เป็น %
String latestWeather = "Sunny";

// ==================== Timing Variables ====================
unsigned long lastSensorReadTime = 0;
const unsigned long sensorReadInterval = 2000; // อ่าน Sensor ทุก 2 วินาที

unsigned long lastWebSendTime = 0;
const unsigned long webSendInterval = 5000; // ส่งไปยัง Web/MQTT ทุก 5 วินาที

unsigned long lastChatTime = 0;
const unsigned long chatInterval = 300000; // แชทต้นไม้ทุก 5 นาที

// ==================== Debug Mode ====================
bool debugMode = true;

// ==================== Setup ====================
void setup() {
  Serial.begin(9600);
  delay(1000);
  
  // Initialize DHT sensor
  dht.begin();
  
  // Setup pump pin
  pinMode(PUMP_PIN, OUTPUT);
  digitalWrite(PUMP_PIN, LOW);
  
  // Connect to WiFi
  Serial.println("\n╔════════════════════════════════════════╗");
  Serial.println("║  FLOWEY COMPLETE SYSTEM STARTING...   ║");
  Serial.println("╚════════════════════════════════════════╝");
  Serial.print("\n📡 Connecting to WiFi: ");
  Serial.println(ssid);
  
  WiFi.begin(ssid, password);
  
  // Wait for WiFi - don't give up!
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  
  Serial.println("\n✓ WiFi Connected!");
  Serial.print("📍 IP Address: ");
  Serial.println(WiFi.localIP());
  Serial.print("📶 Signal Strength: ");
  Serial.print(WiFi.RSSI());
  Serial.println(" dBm");
  
  // Initialize Flowey MQTT (ALWAYS after WiFi is connected)
  flowey.begin(DEVICE_TOKEN);
  
  // Register Callbacks (in this specific order!)
  flowey.setCommandCallback(onCommand);
  flowey.setSensorRequestCallback(onSensorRequest);
  flowey.setDataCallback(onDataReceived);
  
  Serial.println("✓ Flowey MQTT Initialized");
  
  // Send notification to confirm connection
  flowey.sendNotification("Flowey Device Started!");
  
  Serial.println("⏳ Waiting for plant data from server...");

  // Give DHT22 and MQTT time to stabilize
  delay(2000);
  
  Serial.println("\n╔════════════════════════════════════════╗");
  Serial.println("║         SYSTEM READY! 🌱              ║");
  Serial.println("║  Sending to: Pi (USB) + Web (WiFi)   ║");
  Serial.println("╚════════════════════════════════════════╝\n");
  
  if (debugMode) {
    Serial.println("🔍 Debug Mode: ON");
    Serial.println("📊 Reading sensors from pins");
    Serial.println("📤 Dual output: Pi + Web");
    Serial.println("☀️  Light sensor output: Lux only\n");
  }
}

// ==================== Main Loop ====================
void loop() {
  // Maintain MQTT connection (ถ้ามี WiFi)
  static unsigned long lastMqttCheck = 0;
  if (WiFi.status() == WL_CONNECTED) {
    flowey.loop();
    
    // Debug MQTT connection every 30 seconds
    if (millis() - lastMqttCheck > 30000) {
      lastMqttCheck = millis();
      Serial.println("🔄 MQTT Connection Active - Waiting for server data...");
    }
  }
  
  // Read sensors periodically
  readSensors();
  
  // Send to Web/MQTT (ถ้ามี WiFi)
  if (WiFi.status() == WL_CONNECTED) {
    autoSendToWeb();
  }
  
  // Auto chat with plant (optional - เอา comment ออกถ้าต้องการ)
  // if (WiFi.status() == WL_CONNECTED) {
  //   autoChatWithPlant();
  // }
}

// ==================== Read Sensors ====================
void readSensors() {
  unsigned long currentTime = millis();
  
  if (currentTime - lastSensorReadTime >= sensorReadInterval) {
    lastSensorReadTime = currentTime;
    
    // Read DHT22 sensor
    float humidity = dht.readHumidity();
    float temperature = dht.readTemperature();
    
    // Read LDR → กลับค่า → แปลงเป็น Lux ทันที
    int ldrRaw = analogRead(LDR_PIN);
    int ldrInverted = 1023 - ldrRaw; // กลับค่า: ยิ่งสว่างยิ่งมาก
    
    // แปลง LDR เป็น Lux (ค่าสูงสุด 10,000 Lux)
    float calculatedLux = 0.0;
    if (ldrInverted > 0) {
      // ใช้ exponential curve
      float normalized = ldrInverted / 1023.0;
      calculatedLux = 12000.0 * pow(normalized, 1.5); // ค่าสูงสุด 12,000 Lux
    }
    
    // Read soil moisture sensor และแปลงเป็น %
    int soilRaw = analogRead(SOIL_PIN);
    int soilFixed = 1023 - soilRaw; // กลับค่า: 0=แห้ง, 1023=ชื้น
    int soilPercent = map(soilFixed, 0, 1023, 0, 100);
    
    // Check if DHT reading is valid
    bool dhtValid = true;
    if (!isnan(humidity) && !isnan(temperature)) {
      latestTemp = temperature;
      latestHumidity = humidity;
    } else {
      dhtValid = false;
      if (debugMode) {
        Serial.println("⚠️  DHT22 reading failed!");
      }
    }
    
    // เก็บค่าลง global variables (เก็บแค่ Lux ไม่เก็บ LDR)
    latestLux = calculatedLux;
    latestSoil = soilPercent;
    
    // Update weather based on conditions
    updateWeather();
    
    // Create data string for Raspberry Pi (ส่ง Lux + Species + Age + Nickname)
    String dataString = "";
    if (dhtValid) {
      dataString = "Temp:" + String(temperature, 2) + 
                  ",Humidity:" + String(humidity, 2) + 
                  ",Lux:" + String(calculatedLux, 2) +
                  ",Soil:" + String(soilFixed) +
                  ",Nickname:" + PLANT_NICKNAME +
                  ",Species:" + PLANT_SPECIES +
                  ",Age:" + PLANT_AGE;
    } else {
      dataString = "Temp:0.00,Humidity:0.00,Lux:" + 
                  String(calculatedLux, 2) + ",Soil:" + String(soilFixed) +
                  ",Nickname:" + PLANT_NICKNAME +
                  ",Species:" + PLANT_SPECIES +
                  ",Age:" + PLANT_AGE;
    }
        
    // ส่งข้อมูลไปยัง Raspberry Pi ผ่าน Serial (USB)
    Serial.println(dataString);
    
    // แสดงข้อมูลที่อ่านได้ (Debug)
    if (debugMode) {
      Serial.println("\n┌─────────────────────────────────┐");
      Serial.println("│     📊 SENSOR READING           │");
      Serial.println("├─────────────────────────────────┤");
      Serial.print("│ 🌡️  Temperature: "); 
      Serial.print(latestTemp, 2); 
      Serial.println(" °C");
      Serial.print("│ 💧 Humidity:    "); 
      Serial.print(latestHumidity, 2); 
      Serial.println(" %");
      Serial.print("│ ☀️  Light:       "); 
      Serial.print(latestLux, 2); 
      Serial.println(" Lux");
      Serial.print("│ 🌱 Soil:        "); 
      Serial.print(latestSoil); 
      Serial.println(" %");
      Serial.print("│ 🌤️  Weather:     "); 
      Serial.println(latestWeather);
      Serial.println("└─────────────────────────────────┘");
      Serial.print("📤 Sent to Pi: ");
      Serial.println(dataString);
      Serial.println();
    }
  }
}

void updateWeather() {
  // ใช้ Lux ในการตรวจสอบสภาพอากาศ
  if (latestLux > 5000) {
    latestWeather = "Sunny";
  } else if (latestLux > 1000) {
    latestWeather = "Cloudy";
  } else if (latestHumidity > 80) {
    latestWeather = "Rainy";
  } else {
    latestWeather = "Night";
  }
}

// ==================== Auto Send to Web/MQTT ====================
void autoSendToWeb() {
  unsigned long currentTime = millis();
  
  if (currentTime - lastWebSendTime >= webSendInterval) {
    lastWebSendTime = currentTime;
    
    if (debugMode) {
      Serial.println("\n╔════════════════════════════════════════╗");
      Serial.println("║   📤 SENDING TO WEB/MQTT...           ║");
      Serial.println("╚════════════════════════════════════════╝");
      
      Serial.println("\n📦 Data Package:");
      Serial.println("┌─────────────────────────────────┐");
      Serial.print("│ Soil Moisture:  "); Serial.print(latestSoil); Serial.println(" %");
      Serial.print("│ Temperature:    "); Serial.print(latestTemp, 2); Serial.println(" °C");
      Serial.print("│ Humidity:       "); Serial.print(latestHumidity, 2); Serial.println(" %");
      Serial.print("│ Light Intensity: "); Serial.print(latestLux, 2); Serial.println(" Lux");
      Serial.println("└─────────────────────────────────┘");
    }
    
    // ส่งไปยัง MQTT (Flowey)
    flowey.publishSensors(
      latestSoil,      // Soil moisture (%)
      latestTemp,      // Temperature (°C)
      latestHumidity,  // Humidity (%)
      latestLux        // Light intensity (Lux)
    );
    
    if (debugMode) {
      Serial.println("✅ Data sent to MQTT successfully!\n");
      Serial.print("⏰ Sent at: ");
      Serial.print(millis() / 1000);
      Serial.println(" seconds since boot\n");
    }
  }
}

// ==================== MQTT Callbacks ====================
void onCommand(String cmd) {
  Serial.println("\n╔════════════════════════════════════════╗");
  Serial.println("║     📥 COMMAND RECEIVED               ║");
  Serial.println("╚════════════════════════════════════════╝");
  Serial.print("Command: ");
  Serial.println(cmd);
  Serial.println();
  
  if (cmd == "WATER_ON") {
    digitalWrite(PUMP_PIN, HIGH);
    flowey.sendNotification("💧 Pump Activated");
    Serial.println("✓ Pump turned ON");
    
  } else if (cmd == "WATER_OFF") {
    digitalWrite(PUMP_PIN, LOW);
    flowey.sendNotification("🛑 Pump Deactivated");
    Serial.println("✓ Pump turned OFF");
    
  } else if (cmd.startsWith("CHAT:")) {
    String message = cmd.substring(5);
    sendPlantChat(message);
    
  } else {
    Serial.println("⚠️  Unknown command");
  }
  Serial.println();
}

void onSensorRequest() {
  Serial.println("\n╔════════════════════════════════════════╗");
  Serial.println("║  📊 SENSOR DATA REQUESTED BY SERVER   ║");
  Serial.println("╚════════════════════════════════════════╝");
  
  if (debugMode) {
    Serial.println("\n📦 Sending Package:");
    Serial.println("┌─────────────────────────────────┐");
    Serial.print("│ Soil:  "); Serial.print(latestSoil); Serial.println(" %");
    Serial.print("│ Temp:  "); Serial.print(latestTemp); Serial.println(" °C");
    Serial.print("│ Humid: "); Serial.print(latestHumidity); Serial.println(" %");
    Serial.print("│ Light: "); Serial.print(latestLux); Serial.println(" Lux");
    Serial.println("└─────────────────────────────────┘");
  }
  
  flowey.publishSensors(
    latestSoil,
    latestTemp,
    latestHumidity,
    latestLux
  );
  
  Serial.println("✅ Data sent to server\n");
}

void onDataReceived(FloweyPlantData data) {
  Serial.println("\n╔════════════════════════════════════════╗");
  Serial.println("║   📥 PLANT DATA RECEIVED FROM API     ║");
  Serial.println("╚════════════════════════════════════════╝");
  
  Serial.println("\n📦 Received Data:");
  Serial.println("┌─────────────────────────────────┐");
  Serial.print("│ Plant Nickname: "); Serial.println(data.plantNickname);
  Serial.print("│ User Nickname:  "); Serial.println(data.userNickname);
  Serial.print("│ Species:        "); Serial.println(data.species);
  Serial.print("│ Birth Date:     "); Serial.println(data.birth);
  Serial.println("└─────────────────────────────────┘");
  
  // Update global variables
  PLANT_NICKNAME = data.plantNickname;
  PLANT_SPECIES = data.species;
  USER_NICKNAME = data.userNickname;
  
  // Calculate age from birth date (optional - you can keep it simple)
  PLANT_AGE = calculateAge(data.birth);
  
  Serial.println("\n✅ Plant data updated!");
  Serial.print("📤 Data will be sent to Pi on next sensor reading\n");
  Serial.println();
}

// Helper function to calculate age from birth date
String calculateAge(String birthDate) {
  // Simple version - you can enhance this
  if (birthDate.length() == 0) {
    return "Unknown";
  }
  
  // For now, just return "X days old" or keep birth date
  // You can implement proper date calculation if needed
  return "Born " + birthDate;
}

// ==================== Plant Chat API ====================
void sendPlantChat(String userMessage) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("❌ WiFi not connected - Cannot send chat");
    return;
  }
  
  Serial.println("\n╔════════════════════════════════════════╗");
  Serial.println("║      🌱 PLANT CHAT REQUEST            ║");
  Serial.println("╚════════════════════════════════════════╝");
  Serial.print("💬 Your message: ");
  Serial.println(userMessage);
  
  WiFiSSLClient wifiClient;
  HttpClient client = HttpClient(wifiClient, "n8n.etamarov.me", 443);
  
  StaticJsonDocument<512> doc;
  
  String sessionId = "USER_" + String(random(10000, 99999));
  doc["sessionId"] = sessionId;
  doc["msg"] = userMessage;
  doc["plantNickname"] = PLANT_NICKNAME.c_str();
  doc["plantSpecies"] = PLANT_SPECIES.c_str();
  doc["plantAge"] = PLANT_AGE;
  doc["soilMoisture"] = latestSoil;
  doc["temperature"] = latestTemp;
  doc["weather"] = latestWeather;
  doc["humidity"] = latestHumidity;
  doc["lightIntensity"] = latestLux;
  
  String jsonString;
  serializeJson(doc, jsonString);
  
  if (debugMode) {
    Serial.println("\n📤 Sending Request:");
    Serial.println("┌─────────────────────────────────┐");
    Serial.print("│ Session ID: "); Serial.println(sessionId);
    Serial.print("│ Plant:      "); Serial.println(PLANT_NICKNAME);
    Serial.print("│ Species:    "); Serial.println(PLANT_SPECIES);
    Serial.print("│ Soil:       "); Serial.print(latestSoil); Serial.println(" %");
    Serial.print("│ Temp:       "); Serial.print(latestTemp); Serial.println(" °C");
    Serial.print("│ Humidity:   "); Serial.print(latestHumidity); Serial.println(" %");
    Serial.print("│ Light:      "); Serial.print(latestLux); Serial.println(" Lux");
    Serial.print("│ Weather:    "); Serial.println(latestWeather);
    Serial.println("└─────────────────────────────────┘");
    Serial.println("\n📡 Connecting to server...");
  }
  
  client.beginRequest();
  client.post("/webhook/flowey/plant-chat");
  client.sendHeader("Content-Type", "application/json");
  client.sendHeader("Content-Length", jsonString.length());
  client.beginBody();
  client.print(jsonString);
  client.endRequest();
  
  int statusCode = client.responseStatusCode();
  String response = client.responseBody();
  
  Serial.print("\n📥 HTTP Status: ");
  Serial.println(statusCode);
  
  if (statusCode == 200) {
    Serial.println("\n╔════════════════════════════════════════╗");
    Serial.println("║      🌿 PLANT RESPONSE                ║");
    Serial.println("╚════════════════════════════════════════╝");
    
    StaticJsonDocument<1024> responseDoc;
    DeserializationError error = deserializeJson(responseDoc, response);
    
    if (!error) {
      const char* aiReply = responseDoc["reply"];
      const char* recommendation = responseDoc["recommendation"];
      const char* mood = responseDoc["mood"];
      
      if (aiReply) {
        Serial.println("\n💬 Plant says:");
        Serial.println("┌─────────────────────────────────┐");
        Serial.print("│ ");
        Serial.println(aiReply);
        Serial.println("└─────────────────────────────────┘");
        
        flowey.sendNotification(String(aiReply));
      }
      
      if (recommendation) {
        Serial.print("\n💡 Recommendation: ");
        Serial.println(recommendation);
      }
      
      if (mood) {
        Serial.print("😊 Mood: ");
        Serial.println(mood);
      }
    } else {
      Serial.println("⚠️  Raw response:");
      Serial.println(response);
    }
  } else {
    Serial.println("❌ Request failed");
  }
  
  client.stop();
  Serial.println("\n═══════════════════════════════════════════\n");
}

// ==================== Auto Chat (Optional) ====================
void autoChatWithPlant() {
  unsigned long currentTime = millis();
  
  if (currentTime - lastChatTime >= chatInterval) {
    lastChatTime = currentTime;
    
    String questions[] = {
      "How are you feeling today?",
      "Do you need water?",
      "Are you getting enough light?",
      "How is the temperature?",
      "Is everything okay?"
    };
    
    int randomIndex = random(0, 5);
    sendPlantChat(questions[randomIndex]);
  }
}

/*
 * ========================================
 * ข้อมูลที่ส่ง:
 * ========================================
 * 
 * Raspberry Pi:
 *   Temp: °C
 *   Humidity: %
 *   Lux: 0-10,000 Lux ✅
 *   Soil: 0-1023
 * 
 * Web/MQTT:
 *   Temp: °C
 *   Humidity: %
 *   Light: 0-10,000 Lux ✅
 *   Soil: % ✅
 * 
 * ========================================
 */