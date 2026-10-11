import os
from functools import wraps
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from main import (
    submit_scan,
    get_all_scans,
    get_all_products,
    add_product,
    toggle_product_status,
    update_scan_status,
    seed_demo,
    connect_serial,
    firebase_status,
    SERIAL_PORT,
    BAUD_RATE
)

app = Flask(__name__)
# Enable CORS for all routes so the Vite React frontend can communicate seamlessly
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Ensure serial (or Mock LCD simulator) is initialized
connect_serial(SERIAL_PORT, BAUD_RATE)

ROLE_LABELS = {
    "field_officer": "Post-market surveillance and inspection officer",
    "ict_staff": "ICT and regulatory-systems staff",
    "manager": "Manager, supervisor, or administrator"
}

def require_roles(*allowed_roles):
    def decorate(handler):
        @wraps(handler)
        def wrapped(*args, **kwargs):
            authorization = request.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                return jsonify({"error": "Sign in is required."}), 401
            try:
                from firebase_admin import auth as firebase_auth
                claims = firebase_auth.verify_id_token(authorization[7:])
            except Exception:
                return jsonify({"error": "Your session is invalid or expired. Sign in again."}), 401

            email = str(claims.get("email", "")).strip().lower()
            role = None
            for candidate in ROLE_LABELS:
                assigned = {
                    value.strip().lower()
                    for value in os.getenv(f"ROLE_{candidate.upper()}_EMAILS", "").split(",")
                    if value.strip()
                }
                if email and email in assigned:
                    role = candidate
                    break
            if role is None:
                return jsonify({"error": "This account has no MedVerify role assigned. Contact the system administrator."}), 403
            if role not in allowed_roles:
                return jsonify({"error": "Your role does not have permission to perform this action."}), 403

            g.user = {"uid": claims.get("uid"), "email": email, "role": role}
            return handler(*args, **kwargs)
        return wrapped
    return decorate

@app.route("/", methods=["GET"])
def api_root():
    return jsonify({
        "service": "MedVerify Backend Gateway",
        "status": "online",
        "health": "/api/health",
        "endpoints": ["/api/scan", "/api/scan-image", "/api/scans", "/api/products", "/api/seed"]
    })

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "online",
        "service": "MedVerify Backend Gateway",
        "serialPort": SERIAL_PORT,
        "firebase": firebase_status()
    })

@app.route("/api/me", methods=["GET"])
@require_roles("field_officer", "ict_staff", "manager")
def current_user():
    return jsonify({**g.user, "roleLabel": ROLE_LABELS[g.user["role"]]})

@app.route("/api/scan", methods=["POST"])
@require_roles("field_officer")
def api_scan():
    """Submit a medicine scan from web scanner or API."""
    data = request.get_json(force=True, silent=True) or {}
    barcode = str(data.get("barcode", "")).strip()
    if not barcode:
        return jsonify({"error": "barcode field is required"}), 400

    similarity = int(data.get("similarity", 95))
    location   = str(data.get("location", "Kigali"))
    device     = str(data.get("device", "web-scanner"))

    try:
        record = submit_scan(
            barcode=barcode,
            similarity=similarity,
            location=location,
            device=device
        )
        return jsonify(record), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/scan-image", methods=["POST"])
@require_roles("field_officer")
def api_scan_image():
    """Receive an image (base64) and analyze barcode with OpenCV & PyZbar."""
    import base64
    import numpy as np

    try:
        import cv2
        from pyzbar.pyzbar import decode
    except Exception as e:
        return jsonify({"error": f"Computer vision decoder unavailable: {e}"}), 500

    data = request.get_json(force=True, silent=True) or {}
    image_b64 = data.get("image", "")
    similarity = int(data.get("similarity", 95))
    location = str(data.get("location", "Kigali"))

    if not image_b64:
        return jsonify({"error": "No image data provided"}), 400

    if "," in image_b64:
        image_b64 = image_b64.split(",", 1)[1]

    try:
        img_bytes = base64.b64decode(image_b64)
        nparr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            return jsonify({"error": "Could not decode image"}), 400

        # Try direct grayscale decode
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        barcodes = decode(gray)

        # Fallback to thresholding if low contrast
        if not barcodes:
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            thresh = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
            barcodes = decode(thresh)

        if not barcodes:
            return jsonify({
                "found": False,
                "error": "No barcode detected in the photo. Please center the barcode and ensure good lighting."
            }), 422

        barcode_data = barcodes[0].data.decode("utf-8")
        barcode_type = barcodes[0].type

        record = submit_scan(
            barcode=barcode_data,
            similarity=similarity,
            location=location,
            device="image-capture"
        )
        return jsonify({
            "found": True,
            "barcode": barcode_data,
            "type": barcode_type,
            "record": record
        }), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/scans", methods=["GET"])
@require_roles("field_officer", "manager", "ict_staff")
def api_scans():
    """Retrieve all scan verification records."""
    try:
        scans = get_all_scans()
        return jsonify(scans), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/scans/<scan_id>", methods=["PATCH"])
@require_roles("field_officer", "manager")
def api_update_scan(scan_id):
    """Update case status or manager-assigned priority."""
    data = request.get_json(force=True, silent=True) or {}
    case_status = data.get("caseStatus")
    priority = data.get("priority")
    changes = {}
    if case_status is not None:
        if case_status not in {"open", "confirmed", "not_confirmed", "closed"}:
            return jsonify({"error": "caseStatus is invalid"}), 400
        changes["caseStatus"] = case_status
    if priority is not None:
        if g.user["role"] != "manager":
            return jsonify({"error": "Only managers can set case priority."}), 403
        if priority not in {"low", "normal", "high", "urgent"}:
            return jsonify({"error": "priority must be low, normal, high, or urgent"}), 400
        changes["priority"] = priority
    if not changes:
        return jsonify({"error": "caseStatus or priority is required"}), 400

    updated = update_scan_status(scan_id, changes)
    if not updated:
        return jsonify({"error": "Scan record not found"}), 404
    return jsonify(updated), 200

@app.route("/api/products", methods=["GET"])
@require_roles("field_officer", "manager", "ict_staff")
def api_products():
    """Retrieve all registered medicines."""
    try:
        products = get_all_products()
        return jsonify(products), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/products", methods=["POST"])
@require_roles("ict_staff")
def api_add_product():
    """Add a new medicine to the registry."""
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("id"):
        return jsonify({"error": "Product id (barcode) is required"}), 400

    try:
        created = add_product(data)
        return jsonify(created), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/products/<product_id>", methods=["PATCH"])
@require_roles("ict_staff")
def api_toggle_product(product_id):
    """Toggle product status between valid and flagged."""
    res = toggle_product_status(product_id)
    if not res:
        return jsonify({"error": "Product not found"}), 404
    return jsonify(res), 200

@app.route("/api/seed", methods=["POST"])
@require_roles("ict_staff")
def api_seed():
    """Seed demo medicine products and sample verification records."""
    try:
        res = seed_demo()
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"\n=======================================================")
    print(f" MedVerify API Gateway running on http://localhost:{port}")
    print(f" Endpoints: /api/health, /api/scan, /api/scans, /api/products")
    print(f"=======================================================\n")
    app.run(host="0.0.0.0", port=port, debug=False)
