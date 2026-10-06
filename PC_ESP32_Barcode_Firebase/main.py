import json
import threading
import time
from datetime import datetime, timezone
import tkinter as tk
from tkinter import ttk, messagebox

import requests
import serial
from serial.tools import list_ports

import config


class FirebaseREST:
    """Small Firebase Auth + Realtime Database REST client."""

    def __init__(self):
        self.api_key = config.FIREBASE_API_KEY
        self.db_url = config.FIREBASE_DATABASE_URL.rstrip("/")
        self.email = config.FIREBASE_USER_EMAIL
        self.password = config.FIREBASE_USER_PASSWORD

        self.id_token = None
        self.refresh_token = None
        self.uid = None
        self.session = requests.Session()

    def login(self):
        url = (
            "https://identitytoolkit.googleapis.com/v1/"
            f"accounts:signInWithPassword?key={self.api_key}"
        )
        payload = {
            "email": self.email,
            "password": self.password,
            "returnSecureToken": True,
        }

        response = self.session.post(url, json=payload, timeout=15)

        if not response.ok:
            try:
                detail = response.json().get("error", {}).get("message", response.text)
            except Exception:
                detail = response.text
            raise RuntimeError(f"Firebase login failed: {detail}")

        data = response.json()
        self.id_token = data["idToken"]
        self.refresh_token = data.get("refreshToken")
        self.uid = data.get("localId")
        return self.uid

    def _url(self, path):
        clean = path.strip("/")
        return f"{self.db_url}/{clean}.json"

    def request(self, method, path, **kwargs):
        if not self.id_token:
            self.login()

        params = kwargs.pop("params", {})
        params["auth"] = self.id_token

        response = self.session.request(
            method,
            self._url(path),
            params=params,
            timeout=15,
            **kwargs,
        )

        # Firebase ID tokens expire. Re-login once if the request is unauthorized.
        if response.status_code == 401:
            self.login()
            params["auth"] = self.id_token
            response = self.session.request(
                method,
                self._url(path),
                params=params,
                timeout=15,
                **kwargs,
            )

        if not response.ok:
            try:
                detail = response.json().get("error", response.text)
            except Exception:
                detail = response.text
            raise RuntimeError(f"Firebase {method} {path} failed: {detail}")

        if response.status_code == 204 or not response.text:
            return None
        return response.json()

    def write_latest_scan(self, barcode, product=None):
        data = {
            "barcode": barcode,
            "product": product,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scanner_uid": self.uid,
        }
        self.request("PUT", config.LATEST_SCAN_PATH, json=data)

    def add_scan_history(self, barcode, product=None):
        data = {
            "barcode": barcode,
            "product": product,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scanner_uid": self.uid,
        }
        # POST creates a Firebase push key.
        return self.request("POST", config.SCAN_HISTORY_PATH, json=data)

    def get_product(self, barcode):
        # Barcode is used as a Firebase child key. Escape characters that are
        # illegal in Firebase paths by using a lookup structure instead.
        # For normal numeric/EAN/UPC barcodes this direct path is valid.
        if any(ch in barcode for ch in ".#$[]/"):
            return None
        return self.request("GET", f"{config.PRODUCTS_PATH}/{barcode}")


class BarcodeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PC Barcode Scanner + Firebase + ESP32 LCD")
        self.root.geometry("900x620")
        self.root.minsize(760, 520)

        self.firebase = FirebaseREST()
        self.esp32 = None
        self.scanner_mode = "HID"
        self.last_barcode = ""
        self.scan_count = 0

        self.status_var = tk.StringVar(value="Starting...")
        self.firebase_var = tk.StringVar(value="Firebase: not connected")
        self.serial_var = tk.StringVar(value="ESP32: not connected")
        self.barcode_var = tk.StringVar(value="")
        self.product_var = tk.StringVar(value="Product: —")
        self.count_var = tk.StringVar(value="Scans: 0")
        self.port_var = tk.StringVar(value="Auto")

        self.build_ui()
        self.refresh_ports()

        # HID scanner: barcode scanner acts like a keyboard.
        self.barcode_entry.focus_set()

        threading.Thread(target=self.login_worker, daemon=True).start()

        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def build_ui(self):
        title = ttk.Label(
            self.root,
            text="BARCODE SCANNER SYSTEM",
            font=("Segoe UI", 22, "bold"),
        )
        title.pack(pady=(18, 4))

        subtitle = ttk.Label(
            self.root,
            text="USB Barcode Scanner → Python PC → Firebase → ESP32 → I2C LCD",
            font=("Segoe UI", 11),
        )
        subtitle.pack(pady=(0, 18))

        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill="x", padx=25, pady=5)

        ttk.Label(status_frame, textvariable=self.firebase_var).pack(side="left", padx=10)
        ttk.Label(status_frame, textvariable=self.serial_var).pack(side="right", padx=10)

        input_frame = ttk.LabelFrame(self.root, text="Scan barcode")
        input_frame.pack(fill="x", padx=25, pady=15)

        self.barcode_entry = ttk.Entry(
            input_frame,
            textvariable=self.barcode_var,
            font=("Consolas", 25),
            justify="center",
        )
        self.barcode_entry.pack(fill="x", padx=20, pady=20)
        self.barcode_entry.bind("<Return>", self.on_scan)
        self.barcode_entry.bind("<KP_Enter>", self.on_scan)

        ttk.Label(
            input_frame,
            text="Keep this box focused. Most USB scanners automatically type the barcode and press ENTER.",
        ).pack(pady=(0, 15))

        control_frame = ttk.Frame(self.root)
        control_frame.pack(fill="x", padx=25, pady=5)

        ttk.Label(control_frame, text="ESP32 COM port:").pack(side="left", padx=(0, 8))
        self.port_combo = ttk.Combobox(
            control_frame,
            textvariable=self.port_var,
            state="readonly",
            width=30,
        )
        self.port_combo.pack(side="left")

        ttk.Button(control_frame, text="Refresh Ports", command=self.refresh_ports).pack(
            side="left", padx=8
        )
        ttk.Button(control_frame, text="Connect ESP32", command=self.connect_esp32).pack(
            side="left", padx=8
        )
        ttk.Button(control_frame, text="Test LCD", command=self.test_lcd).pack(
            side="left", padx=8
        )
        ttk.Button(control_frame, text="Clear", command=self.clear_input).pack(
            side="right", padx=8
        )

        result_frame = ttk.LabelFrame(self.root, text="Last decoded barcode")
        result_frame.pack(fill="both", expand=True, padx=25, pady=15)

        ttk.Label(
            result_frame,
            textvariable=self.barcode_var,
            font=("Consolas", 32, "bold"),
        ).pack(pady=(35, 8))

        ttk.Label(
            result_frame,
            textvariable=self.product_var,
            font=("Segoe UI", 14),
        ).pack(pady=5)

        ttk.Label(
            result_frame,
            textvariable=self.count_var,
            font=("Segoe UI", 12),
        ).pack(pady=5)

        ttk.Label(
            self.root,
            textvariable=self.status_var,
            relief="sunken",
            anchor="w",
        ).pack(fill="x", side="bottom")

    def login_worker(self):
        try:
            uid = self.firebase.login()
            self.root.after(
                0,
                lambda: self.firebase_var.set(f"Firebase: connected ({uid[:8]}...)"),
            )
            self.root.after(0, lambda: self.status_var.set("Ready. Scan a barcode."))
        except Exception as exc:
            self.root.after(
                0,
                lambda: self.firebase_var.set("Firebase: connection failed"),
            )
            self.root.after(
                0,
                lambda: self.status_var.set(f"Firebase error: {exc}"),
            )

    def refresh_ports(self):
        ports = [p.device for p in list_ports.comports()]
        values = ["Auto"] + ports
        self.port_combo["values"] = values

        if self.port_var.get() not in values:
            self.port_var.set("Auto")

    def find_esp32_port(self):
        selected = self.port_var.get()

        if selected and selected != "Auto":
            return selected

        ports = list(list_ports.comports())

        # Common ESP32 USB-UART chips.
        preferred_keywords = (
            "CP210",
            "CH340",
            "CH910",
            "USB-SERIAL",
            "Silicon Labs",
            "Espressif",
        )

        for p in ports:
            text = f"{p.description} {p.manufacturer} {p.hwid}".lower()
            if any(k.lower() in text for k in preferred_keywords):
                return p.device

        if len(ports) == 1:
            return ports[0].device

        return None

    def connect_esp32(self):
        try:
            if self.esp32 and self.esp32.is_open:
                self.esp32.close()

            port = self.find_esp32_port()
            if not port:
                raise RuntimeError("ESP32 COM port not found. Select it manually.")

            self.esp32 = serial.Serial(
                port,
                config.ESP32_BAUD_RATE,
                timeout=1,
                write_timeout=2,
            )
            time.sleep(2)  # allow ESP32 USB serial to reset

            self.serial_var.set(f"ESP32: connected on {port}")
            self.status_var.set(f"ESP32 connected on {port}.")
        except Exception as exc:
            self.serial_var.set("ESP32: connection failed")
            self.status_var.set(f"ESP32 error: {exc}")

    def send_to_esp32(self, barcode, product=None):
        if not self.esp32 or not self.esp32.is_open:
            raise RuntimeError("ESP32 is not connected.")

        # Protocol:
        # BARCODE|<barcode>|<product>
        safe_product = (product or "").replace("|", "/").replace("\r", " ").replace("\n", " ")
        message = f"BARCODE|{barcode}|{safe_product}\n"
        self.esp32.write(message.encode("utf-8"))
        self.esp32.flush()

    def on_scan(self, event=None):
        barcode = self.barcode_var.get().strip()

        if not barcode:
            return "break"

        # Ignore accidental whitespace generated by some scanners.
        barcode = "".join(barcode.split())

        self.last_barcode = barcode
        self.scan_count += 1
        self.count_var.set(f"Scans: {self.scan_count}")

        self.status_var.set(f"Processing barcode: {barcode}")
        self.barcode_entry.selection_range(0, tk.END)

        threading.Thread(
            target=self.process_scan,
            args=(barcode,),
            daemon=True,
        ).start()

        return "break"

    def process_scan(self, barcode):
        product = None
        firebase_ok = False
        esp32_ok = False

        # Try Firebase.
        try:
            product_data = self.firebase.get_product(barcode)
            if isinstance(product_data, dict):
                product = (
                    product_data.get("name")
                    or product_data.get("productName")
                    or product_data.get("title")
                )
            elif isinstance(product_data, str):
                product = product_data

            self.firebase.write_latest_scan(barcode, product)
            self.firebase.add_scan_history(barcode, product)
            firebase_ok = True
        except Exception as exc:
            firebase_error = str(exc)
        else:
            firebase_error = ""

        # Send to ESP32.
        try:
            self.send_to_esp32(barcode, product)
            esp32_ok = True
        except Exception as exc:
            esp32_error = str(exc)
        else:
            esp32_error = ""

        def update_ui():
            self.product_var.set(f"Product: {product if product else 'Not found'}")

            messages = []
            if firebase_ok:
                messages.append("Firebase OK")
            else:
                messages.append(f"Firebase FAILED: {firebase_error}")

            if esp32_ok:
                messages.append("LCD OK")
            else:
                messages.append(f"LCD FAILED: {esp32_error}")

            self.status_var.set(" | ".join(messages))

            self.barcode_entry.focus_set()
            self.barcode_entry.selection_range(0, tk.END)

        self.root.after(0, update_ui)

    def clear_input(self):
        self.barcode_var.set("")
        self.product_var.set("Product: —")
        self.barcode_entry.focus_set()

    def test_lcd(self):
        try:
            self.send_to_esp32("123456789012", "LCD TEST")
            self.status_var.set("Test barcode sent to ESP32.")
        except Exception as exc:
            self.status_var.set(f"LCD test failed: {exc}")

    def close(self):
        try:
            if self.esp32 and self.esp32.is_open:
                self.esp32.close()
        except Exception:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    style = ttk.Style(root)

    try:
        style.theme_use("vista")
    except tk.TclError:
        pass

    BarcodeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
