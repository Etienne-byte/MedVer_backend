import os
import sys
import time
import json
import uuid
import argparse
from datetime import datetime
from dotenv import load_dotenv
import requests

# Load environment configuration from .env if present
load_dotenv()

# ============================== CONFIGURATION ==============================
FIREBASE_URL  = os.getenv("FIREBASE_URL", "https://medverify-66b55-default-rtdb.firebaseio.com").rstrip("/")
PROJECT_ID    = os.getenv("FIREBASE_PROJECT_ID", "medverify-66b55")
FIREBASE_CRED = os.getenv("FIREBASE_CRED", "serviceAccountKey.json")

SERIAL_PORT   = os.getenv("SERIAL_PORT", "MOCK")   # 'MOCK', 'AUTO', or COM port e.g. 'COM6'
BAUD_RATE     = int(os.getenv("BAUD_RATE", "9600"))
CAMERA_INDEX  = int(os.getenv("CAMERA_INDEX", "0"))
DUPLICATE_COOLDOWN = float(os.getenv("DUPLICATE_COOLDOWN", "3.0"))

DEFAULT_LOCATION   = os.getenv("LOCATION", "Kigali")
DEVICE_ID          = os.getenv("DEVICE_ID", "edge-scanner-01")
DEFAULT_SIMILARITY = int(os.getenv("DEFAULT_SIMILARITY", "95"))

DATA_FILE = os.path.join(os.path.dirname(__file__), "store.json")

# ============================== FIREBASE ADMIN (OPTIONAL) ===================
fs_client = None
if os.path.exists(FIREBASE_CRED):
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_CRED)
            firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_URL})
        fs_client = firestore.client()
        print("[Firebase] Admin SDK initialized successfully with serviceAccountKey.")
    except Exception as e:
        print(f"[Firebase] Admin init note: {e}. Using REST & Local store.")
else:
    print("[Firebase] Running in direct REST / Local store mode (no serviceAccountKey.json required).")

# ============================== LOCAL PERSISTENCE ===========================
DEFAULT_PRODUCTS = {
    "6001234500011": {
        "name": "Coartem 20/120mg", "category": "Antimalarial",
        "manufacturer": "Novartis", "batch": "B-2026-01",
        "expiry": "2028-06-30", "status": "valid"
    },
    "6001234500028": {
        "name": "Amoxicillin 500mg", "category": "Antibiotic",
        "manufacturer": "Cipla", "batch": "B-2026-02",
        "expiry": "2027-12-31", "status": "valid"
    },
    "6001234500035": {
        "name": "Artesunate 60mg", "category": "Antimalarial",
        "manufacturer": "Fosun Pharma", "batch": "B-2026-03",
        "expiry": "2028-03-31", "status": "valid"
    },
    "6001234500042": {
        "name": "Albendazole 400mg", "category": "Anthelmintic",
        "manufacturer": "GSK", "batch": "B-2026-04",
        "expiry": "2029-01-15", "status": "valid"
    }
}

def load_store():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"products": DEFAULT_PRODUCTS.copy(), "scans": []}

def save_store(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[Store] Write error: {e}")

_db_store = load_store()

# ============================== MOCK LCD DISPLAY ============================
class MockSerialLCD:
    """Simulates the ESP32 + 20x4 I2C LCD in terminal when hardware is unavailable."""
    def __init__(self):
        self.is_open = True
        print("\n" + "=" * 45)
        print(" [SIMULATOR] Mock ESP32 + 20x4 LCD Active")
        print(" (No physical Arduino hardware required)")
        print("=" * 45 + "\n")

    def write(self, data: bytes):
        text = data.decode("utf-8", errors="ignore").strip()
        if text.startswith("BARCODE|"):
            parts = text.split("|")
            val = parts[1] if len(parts) > 1 else ""
            btype = parts[2] if len(parts) > 2 else ""

            line1 = "Barcode:"[:20].ljust(20)
            line2 = val[:20].ljust(20)
            line3 = "Type:"[:20].ljust(20)
            line4 = btype[:20].ljust(20)

            print("\n+--- [SIMULATED 20x4 LCD SCREEN] ---+")
            print(f"| {line1} |")
            print(f"| {line2} |")
            print(f"| {line3} |")
            print(f"| {line4} |")
            print("+-----------------------------------+\n")

    def close(self):
        self.is_open = False

# ============================== SERIAL HARDWARE =============================
_global_serial = None

def find_esp32_port():
    try:
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            desc = (p.description or "").upper()
            if any(k in desc for k in ["CP210", "CH340", "USB", "SERIAL", "UART"]):
                return p.device
    except Exception:
        pass
    return None

def connect_serial(port_name=SERIAL_PORT, baud=BAUD_RATE):
    global _global_serial
    if _global_serial is not None and getattr(_global_serial, "is_open", False):
        return _global_serial

    if port_name.upper() == "MOCK":
        _global_serial = MockSerialLCD()
        return _global_serial

    try:
        import serial
        target = port_name
        if port_name.upper() == "AUTO":
            detected = find_esp32_port()
            if detected:
                print(f"[Serial] Auto-detected hardware on {detected}")
                target = detected
            else:
                print("[Serial] No hardware port detected. Using Mock LCD.")
                _global_serial = MockSerialLCD()
                return _global_serial

        ser = serial.Serial(target, baud, timeout=1)
        time.sleep(2)
        print(f"[Serial] Connected to {target} @ {baud}")
        _global_serial = ser
        return ser
    except Exception as e:
        print(f"[Serial] Could not open {port_name}: {e}. Falling back to Mock LCD.")
        _global_serial = MockSerialLCD()
        return _global_serial

def send_to_esp32(barcode_value, barcode_type="EAN13"):
    global _global_serial
    ser = _global_serial or connect_serial(SERIAL_PORT, BAUD_RATE)
    if ser is None or not getattr(ser, "is_open", False):
        return
    line = f"BARCODE|{barcode_value}|{barcode_type}\n"
    try:
        ser.write(line.encode("utf-8"))
        print(f"[Serial] Sent to LCD: {line.strip()}")
    except Exception as e:
        print(f"[Serial] Write error: {e}")

# ================== CLASSIFY (mirrors frontend classify.ts) =================
def level(s: int) -> str:
    return "high" if s >= 85 else "moderate" if s >= 60 else "low"

def classify(code_match: str, similarity: int) -> str:
    ok = code_match == "valid_unused"
    l  = level(similarity)
    if ok and l == "high":         return "Genuine"
    if ok and l == "moderate":     return "Low Suspicion"
    if not ok and l == "high":     return "Medium Suspicion"
    if ok and l == "low":          return "High Suspicion"
    if not ok and l == "moderate": return "High Suspicion"
    return "Critical"

# ============================== PRODUCT & SCAN OPERATIONS ===================
def lookup_product(barcode: str):
    """Check Firestore / RTDB / Local store for product registry record."""
    if fs_client:
        try:
            snap = fs_client.collection("products").document(barcode).get()
            if snap.exists:
                return snap.to_dict()
        except Exception:
            pass

    p = _db_store["products"].get(barcode)
    if p:
        return p

    try:
        res = requests.get(f"{FIREBASE_URL}/products/{barcode}.json", timeout=3)
        if res.status_code == 200 and res.json():
            return res.json()
    except Exception:
        pass

    return None

def submit_scan(barcode: str, similarity: int = DEFAULT_SIMILARITY,
                location: str = DEFAULT_LOCATION, device: str = DEVICE_ID) -> dict:
    """Classify medicine, write scan record, update LCD and store."""
    p = lookup_product(barcode)
    if p is None:
        code_match, product_name = "not_found", "Unknown product"
    elif str(p.get("status", "")).lower() in ("flagged", "invalid") or p.get("isValid") is False or p.get("is_valid") is False:
        code_match, product_name = "already_used", p.get("name", "Unknown product")
    elif str(p.get("status", "")).lower() == "valid" or p.get("isValid") is True or p.get("is_valid") is True:
        code_match, product_name = "valid_unused", p.get("name", "Unknown product")
    else:
        code_match, product_name = "status_unknown", p.get("name", "Unknown product")

    category = classify(code_match, similarity)
    now_ms = int(time.time() * 1000)
    scan_id = str(uuid.uuid4())[:8]

    product_fields = p or {}
    nested_product = product_fields.get("product") or product_fields.get("details") or product_fields.get("medicine")
    if isinstance(nested_product, dict):
        product_fields = {**product_fields, **nested_product}

    record = {
        "id": scan_id,
        "barcode": barcode,
        "productName": product_name,
        "medicineType": product_fields.get("category", product_fields.get("medicineType", product_fields.get("type", ""))),
        "manufacturer": product_fields.get("manufacturer", product_fields.get("manufacturerName", product_fields.get("brand", ""))),
        "batch": product_fields.get("batch", product_fields.get("batchNumber", "")),
        "expiry": product_fields.get("expiry", product_fields.get("expiryDate", product_fields.get("expirationDate", ""))),
        "piecesPerPack": product_fields.get("piecesPerPack", product_fields.get("packSize", product_fields.get("quantity", 0))),
        "registryStatus": "not_found" if p is None else product_fields.get("status", "unknown"),
        "productDetails": p,
        "codeMatch": code_match,
        "similarity": similarity,
        "category": category,
        "location": location,
        "device": device,
        "caseStatus": "closed" if category == "Genuine" else "open",
        "createdAt": now_ms
    }

    # 1. Save in local memory/file store
    _db_store["scans"].insert(0, record)
    if len(_db_store["scans"]) > 300:
        _db_store["scans"] = _db_store["scans"][:300]
    save_store(_db_store)

    # 2. Push to Firebase RTDB REST
    try:
        requests.put(f"{FIREBASE_URL}/scans/{scan_id}.json", json=record, timeout=3)
    except Exception:
        pass

    # 3. Push to Firestore if Admin client is active
    if fs_client:
        try:
            fs_client.collection("scans").document(scan_id).set(record)
        except Exception:
            pass

    # 4. Transmit to Arduino / Simulated LCD
    send_to_esp32(barcode, "EAN13")

    print(f"[Scan Saved] {barcode} -> {product_name} -> {category}")
    return record

def get_all_scans():
    return _db_store["scans"]

def get_all_products():
    res = []
    for pid, pdata in _db_store["products"].items():
        res.append({"id": pid, **pdata})
    return res

def add_product(prod_data: dict):
    pid = str(prod_data.get("id", "")).strip()
    if not pid:
        raise ValueError("Product barcode id is required")
    data = {
        "name": prod_data.get("name", "New Product"),
        "category": prod_data.get("category", "Other"),
        "manufacturer": prod_data.get("manufacturer", "Unknown"),
        "batch": prod_data.get("batch", "B-001"),
        "expiry": prod_data.get("expiry", "2027-12-31"),
        "status": prod_data.get("status", "valid")
    }
    _db_store["products"][pid] = data
    save_store(_db_store)
    try:
        requests.put(f"{FIREBASE_URL}/products/{pid}.json", json=data, timeout=3)
    except Exception:
        pass
    return {"id": pid, **data}

def toggle_product_status(pid: str):
    p = _db_store["products"].get(pid)
    if not p:
        return None
    p["status"] = "flagged" if p.get("status") == "valid" else "valid"
    save_store(_db_store)
    try:
        requests.put(f"{FIREBASE_URL}/products/{pid}.json", json=p, timeout=3)
    except Exception:
        pass
    return {"id": pid, **p}

def update_scan_status(scan_id: str, new_status: str):
    for s in _db_store["scans"]:
        if s["id"] == scan_id:
            s["caseStatus"] = new_status
            save_store(_db_store)
            try:
                requests.patch(f"{FIREBASE_URL}/scans/{scan_id}.json", json={"caseStatus": new_status}, timeout=3)
            except Exception:
                pass
            return s
    return None

def seed_demo():
    for pid, pdata in DEFAULT_PRODUCTS.items():
        _db_store["products"][pid] = pdata.copy()

    demo_scans = [
        ("6001234500011", 97, "Kigali"),
        ("6001234500028", 72, "Rubavu"),
        ("9999999999991", 91, "Rusizi"),
        ("9999999999992", 31, "Rubavu"),
    ]
    for bcode, sim, loc in demo_scans:
        submit_scan(bcode, sim, loc, "seed")

    save_store(_db_store)
    return {"ok": True, "message": "Demo data seeded successfully"}

# ============================== OPTIONAL DESKTOP CAMERA =====================
def run_camera():
    """Optional desktop webcam scanner. The web app uses browser webcam."""
    try:
        import cv2
        from pyzbar.pyzbar import decode
    except ImportError as e:
        print(f"[Camera] OpenCV/PyZbar not loaded: {e}. Use browser scanner on web app.")
        return

    connect_serial(SERIAL_PORT, BAUD_RATE)
    cap = cv2.VideoCapture(CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    if not cap.isOpened():
        print(f"[Camera] Cannot open webcam index {CAMERA_INDEX}.")
        print("[Camera] Use browser camera on web app at http://localhost:5173.")
        return

    print("\n[Scanner] Desktop Webcam active! Point a barcode or QR code at camera.")
    print("[Scanner] Press 'Q' on the video window to quit.\n")

    last_val = None
    last_t = 0.0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        barcodes = decode(gray)

        for bc in barcodes:
            b_data = bc.data.decode("utf-8")
            (x, y, w, h) = bc.rect
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(frame, b_data, (x, max(20, y - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            now = time.time()
            if b_data == last_val and (now - last_t) < DUPLICATE_COOLDOWN:
                continue
            last_val = b_data
            last_t = now

            submit_scan(b_data, DEFAULT_SIMILARITY, DEFAULT_LOCATION, DEVICE_ID)

        h_f, w_f = frame.shape[:2]
        cv2.rectangle(frame, (w_f // 4, h_f // 4), (3 * w_f // 4, 3 * h_f // 4), (255, 0, 0), 2)
        cv2.imshow("MedVerify Scanner - Press Q to quit", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()

# ============================== CLI MAIN ENTRY ==============================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MedVerify Backend Gateway & Engine")
    parser.add_argument("--camera", "-c", action="store_true",
                        help="Run optional desktop OpenCV webcam (Default is to run the API server)")
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Run manual terminal scan input mode")
    parser.add_argument("--test-scan", "-t", nargs=2, metavar=("BARCODE", "SIMILARITY"),
                        help="Single test scan: --test-scan 6001234500011 95")
    parser.add_argument("--seed", action="store_true",
                        help="Seed demo pharmaceutical products and scans")
    args = parser.parse_args()

    connect_serial(SERIAL_PORT, BAUD_RATE)

    if args.camera:
        # User explicitly requested desktop camera
        run_camera()
    elif args.seed:
        seed_demo()
        print("[Done] Seeded demo data.")
    elif args.test_scan:
        bcode, sim = args.test_scan
        rec = submit_scan(bcode, int(sim))
        print(f"Result: {rec}")
    elif args.interactive:
        print("\n--- Interactive Test Mode (Type 'exit' to quit) ---")
        while True:
            try:
                val = input("\nEnter Barcode: ").strip()
                if not val or val.lower() in ["exit", "quit", "q"]:
                    break
                sim_str = input("Enter Sensor Similarity (0-100) [95]: ").strip()
                sim = int(sim_str) if sim_str.isdigit() else 95
                submit_scan(val, sim)
            except (KeyboardInterrupt, EOFError):
                break
    else:
        # DEFAULT ACTION: Start the API Gateway Server!
        # The frontend provides the camera feed via browser!
        from api import app
        port = int(os.environ.get("PORT", 8000))
        print(f"\n============================================================")
        print(f" 🚀 MedVerify API Gateway & Processing Brain is running!")
        print(f" 🌐 URL: http://localhost:{port}")
        print(f" 📷 Webcam scanner is in the Web App at http://localhost:5173")
        print(f" 📟 Simulated ESP32/LCD is active in this console")
        print(f"============================================================\n")
        app.run(host="0.0.0.0", port=port, debug=False)