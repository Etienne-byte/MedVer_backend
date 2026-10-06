#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// 20x4 I2C LCD — adjust address if needed (0x27 or 0x3F)
LiquidCrystal_I2C lcd(0x27, 20, 4);

String lastBarcode = "";
String lastType    = "";
unsigned long lastDisplayTime = 0;

void setup() {
  Serial.begin(9600);          // USB serial to PC
  delay(1000);

  Wire.begin();            // ESP32 default I2C: SDA=21, SCL=22
  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0);
  lcd.print("Barcode Scanner");
  lcd.setCursor(0, 1);
  lcd.print("Ready to scan...");

  Serial.println("[ESP32] Ready");
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

        Serial.println("[ESP32] Displayed: " + value + " (" + type + ")");
      }
    }
  }

  // Clear LCD after 15 seconds of inactivity
  if (lastBarcode != "" && (millis() - lastDisplayTime > 15000)) {
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("Ready to scan...");
    lastBarcode = "";
  }
}