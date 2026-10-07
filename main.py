import cv2
import numpy as np
from pyzbar.pyzbar import decode
import requests
import serial
import serial.tools.list_ports
import time
import json
from datetime import datetime

# ============================== CONFIG ==================================
# Firebase RTDB (use your database URL)
FIREBASE_URL = "https://distance-prediction-system-default-rtdb.firebaseio.com"

# ESP32 Serial Port (change to your port, e.g., "COM3" on Windows, "/dev/ttyUSB0" on Linux)
SERIAL_PORT = "COM6"          # <-- CHANGE THIS
BAUD_RATE   = 9600

# Duplicate suppression (seconds)
DUPLICATE_COOLDOWN = 3.0

# ============================== SERIAL SETUP ============================
def find_esp32_port():
    """Auto-detect ESP32 serial port (optional helper)."""
    ports = serial.tools.list_ports.comports()
    for p in ports:
        if "CP210" in p.description or "CH340" in p.description or "USB" in p.description:
            return p.device
    return None

def connect_serial(port, baud):
    try:
        ser = serial.Serial(port, baud, timeout=1)
        time.sleep(2)  # let ESP32 reset
        print(f"[Serial] Connected to {port} @ {baud}")
        return ser
    except Exception as e:
        print(f"[Serial] Could not open {port}: {e}")
        return None

# ============================== FIREBASE ================================
def save_to_firebase(barcode_value, barcode_type):
    """Push a scan record to Firebase Realtime Database via REST API."""
    timestamp = datetime.now().isoformat()
    payload = {
        "barcode": barcode_value,
        "type": barcode_type,
        "timestamp": timestamp
    }
    # POST creates a unique key under /scans
    url = f"{FIREBASE_URL}/scans.json"
    try:
        resp = requests.post(url, data=json.dumps(payload), timeout=5)
        if resp.status_code == 200:
            print(f"[Firebase] Saved: {barcode_value}")
        else:
            print(f"[Firebase] Error {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[Firebase] Request failed: {e}")

# ============================== SERIAL SEND =============================
def send_to_esp32(ser, barcode_value, barcode_type):
    """Send barcode data to ESP32 over USB serial.
    Format: BARCODE|<value>|<type>\n
    """
    if ser is None or not ser.is_open:
        return
    line = f"BARCODE|{barcode_value}|{barcode_type}\n"
    try:
        ser.write(line.encode("utf-8"))
        print(f"[Serial] Sent: {line.strip()}")
    except Exception as e:
        print(f"[Serial] Write failed: {e}")

# ============================== MAIN LOOP ===============================
def main():
    # --- Serial ---
    ser = connect_serial(SERIAL_PORT, BAUD_RATE)

    # --- Camera ---
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    if not cap.isOpened():
        print("[Camera] Cannot open webcam")
        return

    print("[Scanner] Ready. Show a barcode to the camera. Press 'Q' to quit.")

    last_value   = None
    last_time    = 0.0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Decode barcodes / QR codes in the frame
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        barcodes = decode(gray)

        for barcode in barcodes:
            # Extract data
            barcode_data = barcode.data.decode("utf-8")
            barcode_type = barcode.type

            # Draw bounding box
            (x, y, w, h) = barcode.rect
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

            # Draw text above box
            text = f"{barcode_data} ({barcode_type})"
            cv2.putText(frame, text, (x, y - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            # --- Duplicate suppression ---
            now = time.time()
            if barcode_data == last_value and (now - last_time) < DUPLICATE_COOLDOWN:
                continue  # skip duplicate

            last_value = barcode_data
            last_time  = now

            # --- Actions ---
            print(f"\n[Scan] {barcode_data}  ({barcode_type})")
            save_to_firebase(barcode_data, barcode_type)
            send_to_esp32(ser, barcode_data, barcode_type)

        # --- Draw scanning rectangle guide ---
        h_frame, w_frame = frame.shape[:2]
        cv2.rectangle(frame,
                      (w_frame // 4, h_frame // 4),
                      (3 * w_frame // 4, 3 * h_frame // 4),
                      (255, 0, 0), 2)

        cv2.imshow("Barcode Scanner - Press Q to quit", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
    if ser and ser.is_open:
        ser.close()
    print("[Scanner] Stopped.")

if __name__ == "__main__":
    main()