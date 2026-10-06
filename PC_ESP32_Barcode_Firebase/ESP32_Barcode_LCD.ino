#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// =============================
// LCD
// =============================
#define LCD_ADDRESS 0x27
#define LCD_COLUMNS 20
#define LCD_ROWS 4

// ESP32 default I2C pins:
// SDA = GPIO 21
// SCL = GPIO 22

LiquidCrystal_I2C lcd(LCD_ADDRESS, LCD_COLUMNS, LCD_ROWS);

String serialLine = "";

void showCentered(const String &text, uint8_t row) {
  lcd.setCursor(0, row);
  lcd.print("                    "); // 20 spaces

  int start = 0;
  if (text.length() < LCD_COLUMNS) {
    start = (LCD_COLUMNS - text.length()) / 2;
  }

  lcd.setCursor(start, row);
  lcd.print(text.substring(0, LCD_COLUMNS));
}

void showBarcode(String barcode, String product) {
  lcd.clear();

  showCentered("BARCODE SCANNER", 0);
  showCentered(barcode, 1);

  if (product.length() == 0) {
    showCentered("Product: Not found", 2);
  } else {
    showCentered("Product:", 2);
    showCentered(product, 3);
  }
}

void processCommand(String command) {
  command.trim();

  if (!command.startsWith("BARCODE|")) {
    return;
  }

  command.remove(0, 8); // remove "BARCODE|"

  int separator = command.indexOf('|');

  String barcode;
  String product;

  if (separator >= 0) {
    barcode = command.substring(0, separator);
    product = command.substring(separator + 1);
  } else {
    barcode = command;
    product = "";
  }

  barcode.trim();
  product.trim();

  if (barcode.length() == 0) {
    return;
  }

  showBarcode(barcode, product);

  Serial.print("LCD_DISPLAYED|");
  Serial.println(barcode);
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Wire.begin(21, 22);

  lcd.init();
  lcd.backlight();
  lcd.clear();

  showCentered("BARCODE SCANNER", 0);
  showCentered("ESP32 READY", 1);
  showCentered("Waiting for PC...", 2);

  Serial.println("ESP32_BARCODE_READY");
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();

    if (c == '\n') {
      processCommand(serialLine);
      serialLine = "";
    } else if (c != '\r') {
      serialLine += c;

      // Prevent an accidental endless string from consuming RAM.
      if (serialLine.length() > 300) {
        serialLine = "";
      }
    }
  }
}
