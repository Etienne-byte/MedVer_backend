# PC + USB Barcode Scanner + ESP32 LCD + Firebase

## 1. System architecture

```text
                         USB
┌──────────────────┐  barcode text  ┌──────────────────────────┐
│ Physical Barcode │ ──────────────> │ Windows PC / Python     │
│ Scanner          │                 │ Tkinter application     │
└──────────────────┘                 └───────────┬──────────────┘
                                                 │
                          HTTPS / Firebase Auth  │
                                                 ▼
                                      ┌──────────────────────┐
                                      │ Firebase Realtime DB │
                                      │ products/             │
                                      │ scanner/latest        │
                                      │ barcode_scans/        │
                                      └──────────────────────┘

                         USB Serial
┌──────────────────┐  BARCODE|...   ┌──────────────────────────┐
│ ESP32            │ <───────────── │ Python application       │
│ I2C LCD 20x4     │                └──────────────────────────┘
└──────────────────┘
```

The ESP32 does NOT need Firebase credentials or Wi-Fi for this design.
The PC performs Firebase authentication and database operations.

## 2. Hardware

- ESP32 development board
- 20x4 I2C LCD
- USB cable from ESP32 to PC
- USB barcode scanner

### LCD wiring

| LCD I2C | ESP32 |
|---|---|
| GND | GND |
| VCC | 5V/VIN |
| SDA | GPIO 21 |
| SCL | GPIO 22 |

If your LCD uses address `0x3F` instead of `0x27`, change:

```cpp
#define LCD_ADDRESS 0x27
```

to:

```cpp
#define LCD_ADDRESS 0x3F
```

## 3. Install Python

Use Python 3.10+.

Open CMD/PowerShell in this folder:

```powershell
python -m pip install -r requirements.txt
```

If `python` is not recognized, try:

```powershell
py -m pip install -r requirements.txt
```

## 4. Upload ESP32 firmware

Install these Arduino libraries:

- LiquidCrystal_I2C

Select your ESP32 board in Arduino IDE.

Upload:

```text
ESP32_Barcode_LCD.ino
```

After upload, keep the ESP32 connected to the PC.

## 5. Firebase setup

### Authentication

In Firebase Console:

1. Open Authentication.
2. Open Sign-in method.
3. Enable Email/Password.
4. Make sure the account in `config.py` exists.

The Python application uses Firebase Authentication REST API to obtain a Firebase ID token. The ID token is then used for Realtime Database REST requests.

Firebase documents the email/password endpoint as:

```text
https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=API_KEY
```

and the Realtime Database REST API accepts Firebase ID tokens for authenticated requests.

### Realtime Database rules

Use:

```json
{
  "rules": {
    ".read": "auth != null",
    ".write": "auth != null"
  }
}
```

This allows authenticated Firebase users to read/write the database.

## 6. Recommended database structure

Create products such as:

```text
products
  123456789012
    name: "Samsung Galaxy A15"
    price: 250000
    stock: 5

  890123456789
    name: "iPhone 13 Pro Max"
    price: 850000
    stock: 2
```

The barcode itself is the product key.

The application automatically writes:

```text
scanner
  latest
    barcode: "123456789012"
    product: "Samsung Galaxy A15"
    timestamp: "2026-..."
    scanner_uid: "..."

barcode_scans
  -Firebase generated key-
    barcode: "123456789012"
    product: "Samsung Galaxy A15"
    timestamp: "2026-..."
    scanner_uid: "..."
```

## 7. Start the application

Run:

```powershell
python main.py
```

or:

```powershell
py main.py
```

The window will open.

### First

Click:

```text
Refresh Ports
```

Select the ESP32 COM port.

Click:

```text
Connect ESP32
```

Then click:

```text
Test LCD
```

The LCD should display a test barcode.

## 8. Scan

Most USB barcode scanners work as HID keyboard devices.

That means you do NOT need a special Python scanner driver.

The scanner behaves like:

```text
1
2
3
4
5
6
...
ENTER
```

The Python application receives the characters in the barcode input box.

When ENTER arrives:

1. Python reads the barcode.
2. Python searches `products/<barcode>`.
3. Python writes `scanner/latest`.
4. Python creates a record under `barcode_scans`.
5. Python sends the barcode to the ESP32.
6. ESP32 displays it on the LCD.

## 9. ESP32 serial protocol

Python sends:

```text
BARCODE|123456789012|Samsung Galaxy A15
```

ESP32 returns:

```text
LCD_DISPLAYED|123456789012
```

This makes the PC↔ESP32 communication simple and reliable.

## 10. If your scanner is a COM/serial scanner

Many scanners are HID keyboards, but some can operate as serial devices.

This project currently expects the physical barcode scanner to operate as a keyboard. That is intentional because it is the most common USB scanner configuration.

If your scanner appears as a COM port and does not type into Notepad, configure the scanner for USB HID Keyboard mode using its manufacturer's configuration barcode/manual.

## 11. Important security note

`config.py` contains a Firebase account password.

Do NOT upload this project to GitHub or send `config.py` publicly.

For a production deployment, move the password into an environment variable or Windows Credential Manager.

Also, because the password was shared in this conversation, change that Firebase user's password after testing if the account is important.

## 12. Troubleshooting

### LCD shows nothing

Try:

```cpp
#define LCD_ADDRESS 0x3F
```

instead of:

```cpp
#define LCD_ADDRESS 0x27
```

Also check SDA/SCL wiring.

### ESP32 does not appear

Install the correct USB-UART driver if required by your board, then check Windows Device Manager:

```text
Ports (COM & LPT)
```

### Firebase login failed

Check:

- Email/password are correct.
- Email/Password provider is enabled.
- API key belongs to the same Firebase project.
- The Realtime Database URL is correct.

### Firebase 401 / Permission denied

The database rules require:

```text
auth != null
```

so the Python Firebase user must successfully authenticate.

### Scanner does nothing

Open Notepad and scan a barcode.

If the barcode appears in Notepad followed by Enter, the scanner is working in HID mode.

Then click the barcode field in this application and scan again.

### ESP32 serial monitor problem

Close Arduino IDE Serial Monitor before clicking `Connect ESP32` in Python. Only one application should own the COM port at a time.
