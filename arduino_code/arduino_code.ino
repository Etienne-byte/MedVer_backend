#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// 20x4 I2C LCD — adjust address if needed (0x27 or 0x3F)
LiquidCrystal_I2C lcd(0x27, 20, 4);

// Classic Arduino Nano uses D6/D7. Use safe general-purpose pins on ESP32.
#if defined(ARDUINO_ARCH_ESP32)
const uint8_t GREEN_LED_PIN = 24;
const uint8_t RED_LED_PIN = 25;
#else
const uint8_t GREEN_LED_PIN = 5;
const uint8_t RED_LED_PIN = 6;
#endif
const unsigned long RESULT_LED_DURATION_MS = 1000UL;
unsigned long resultLedStartedAt = 0;
bool resultLedActive = false;

String lastBarcode = "";
String lastType    = "";
unsigned long lastDisplayTime = 0;

void setup() {
  Serial.begin(9600);          // USB serial to PC
  delay(1000);

  pinMode(GREEN_LED_PIN, OUTPUT);
  pinMode(RED_LED_PIN, OUTPUT);
  digitalWrite(GREEN_LED_PIN, LOW);
  digitalWrite(RED_LED_PIN, LOW);

  Wire.begin();            // Uses the selected board's default I2C pins.
  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0);
  lcd.print("Barcode Scanner");
  lcd.setCursor(0, 1);
  lcd.print("Ready to scan...");

  Serial.println("[Arduino] Ready");
}

void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();

    if (line.startsWith("BARCODE|")) {
      // Format: BARCODE|<value>|<type>
      int first  = line.indexOf('|');
      int second = line.indexOf('|', first + 1);

      if (first != -1 && second != -1) {
        String value = line.substring(first + 1, second);
        String type  = line.substring(second + 1);

        lastBarcode = value;
        lastType    = type;
        lastDisplayTime = millis();

        lcd.clear();
        lcd.setCursor(0, 0);
        lcd.print("Barcode:");
        lcd.setCursor(0, 1);
        lcd.print(value.substring(0, 20));

        lcd.setCursor(0, 2);
        lcd.print("Type:");
        lcd.setCursor(0, 3);
        lcd.print(type.substring(0, 20));

        Serial.println("[Arduino] Displayed: " + value + " (" + type + ")");
      }
    } else if (line.startsWith("RESULT|")) {
      // Format: RESULT|GENUINE or RESULT|NOT_GENUINE
      String result = line.substring(7);
      result.trim();
      bool isGenuine = result.equalsIgnoreCase("GENUINE");

      digitalWrite(GREEN_LED_PIN, isGenuine ? HIGH : LOW);
      digitalWrite(RED_LED_PIN, isGenuine ? LOW : HIGH);
      resultLedStartedAt = millis();
      resultLedActive = true;

      Serial.println(isGenuine
        ? "[Arduino] Genuine: green LED for 1 second"
        : "[Arduino] Not genuine: red LED for 1 second");
    }
  }

  // Turn both status LEDs off after the one-second result pulse.
  if (resultLedActive && millis() - resultLedStartedAt >= RESULT_LED_DURATION_MS) {
    digitalWrite(GREEN_LED_PIN, LOW);
    digitalWrite(RED_LED_PIN, LOW);
    resultLedActive = false;
  }

  // Clear LCD after 15 seconds of inactivity
  if (lastBarcode != "" && (millis() - lastDisplayTime > 15000)) {
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("Ready to scan...");
    lastBarcode = "";
  }
}
