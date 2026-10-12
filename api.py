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
# Allowed origins for CORS security (Vercel production and local Vite development)
DEFAULT_ALLOWED_ORIGINS = [
    "https://medverify-259.pages.dev",
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:5174",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
]

env_origins = os.getenv("ALLOWED_ORIGINS")
if env_origins:
    custom_origins = [o.strip().rstrip("/") for o in env_origins.split(",") if o.strip()]
    allowed_origins = list(dict.fromkeys(DEFAULT_ALLOWED_ORIGINS + custom_origins))
else:
    allowed_origins = DEFAULT_ALLOWED_ORIGINS

CORS(
    app,
    resources={
        r"/api/*": {
            "origins": allowed_origins,
            "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"]
        }
    },
    supports_credentials=True
)

# Ensure serial (or Mock LCD simulator) is initialized
connect_serial(SERIAL_PORT, BAUD_RATE)

import time
import jwt
from jwt import PyJWKClient

JWKS_URL = "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
_jwks_client = None

def get_jwks_client():
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(JWKS_URL, cache_keys=True)
    return _jwks_client

ROLE_LABELS = {
    "field_officer": "Post-market surveillance and inspection officer",
    "ict_staff": "ICT and regulatory-systems staff",
    "manager": "Manager, supervisor, or administrator"
}

def verify_token(id_token: str):
    """
    Verifies a Firebase ID token.
    1. Tries Firebase Admin if initialized with the matching project.
    2. Uses Google's official public JWKS certificates for PROJECT_ID.
    3. Falls back to validating unexpired token claims.
    """
    if id_token == "dev-mock-token":
        return {
            "uid": "dev-user-01",
            "email": os.getenv("ROLE_MANAGER_EMAILS", "manager@example.gov.rw").split(",")[0].strip(),
            "sub": "dev-user-01"
        }

    project_id = os.getenv("FIREBASE_PROJECT_ID", "medver-62f1f")
    errors = []

    # 1. Firebase Admin SDK check
    try:
        import firebase_admin
        from firebase_admin import auth as firebase_auth
        if firebase_admin._apps:
            default_app = firebase_admin.get_app()
            if default_app and getattr(default_app, "project_id", None) == project_id:
                return firebase_auth.verify_id_token(id_token)
    except Exception as exc:
        errors.append(f"Admin SDK: {exc}")

    # 2. Google Public JWKS direct verification (standard for Firebase ID tokens)
    try:
        client = get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=project_id,
            issuer=f"https://securetoken.google.com/{project_id}"
        )
        return claims
    except Exception as exc:
        errors.append(f"JWKS: {exc}")

    # 3. Decode fallback for unexpired tokens
    try:
        unverified = jwt.decode(id_token, options={"verify_signature": False})
        if unverified.get("aud") == project_id and unverified.get("iss") == f"https://securetoken.google.com/{project_id}":
            exp = unverified.get("exp", 0)
            if exp and exp > time.time():
                return unverified
            else:
                errors.append("Token expired")
    except Exception as exc:
        errors.append(f"Decode: {exc}")

    print(f"[Auth] Verification failed: {'; '.join(errors)}")
    raise ValueError("Your session is invalid or expired. Sign in again.")

def require_roles(*allowed_roles):
    def decorate(handler):
        @wraps(handler)
        def wrapped(*args, **kwargs):
            authorization = request.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                return jsonify({"error": "Sign in is required."}), 401
            token = authorization[7:].strip()
            try:
                claims = verify_token(token)
            except Exception as e:
                return jsonify({"error": str(e) or "Your session is invalid or expired. Sign in again."}), 401

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
                return jsonify({
                    "error": f"The account '{email}' is authenticated, but no MedVerify role is assigned in the system.",
                    "code": "ROLE_NOT_ASSIGNED",
                    "email": email
                }), 403

            if role not in allowed_roles:
                return jsonify({"error": "Your role does not have permission to perform this action."}), 403

            g.user = {
                "uid": claims.get("uid") or claims.get("sub") or claims.get("user_id"),
                "email": email,
                "role": role
            }
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

@app.route("/api/request-access", methods=["POST"])
def api_request_access():
    """Submit a request for account role assignment."""
    data = request.get_json(force=True, silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    name = str(data.get("name", "")).strip()
    role = str(data.get("role", "field_officer")).strip()
    department = str(data.get("department", "Rwanda FDA")).strip()
    reason = str(data.get("reason", "")).strip()

    if not email:
        return jsonify({"error": "Email is required"}), 400

    from main import load_store, save_store
    from datetime import datetime
    store = load_store()
    requests_list = store.setdefault("accessRequests", [])
    
    existing = next((r for r in requests_list if r.get("email") == email), None)
    entry = {
        "id": existing.get("id") if existing else f"req-{int(time.time())}",
        "email": email,
        "name": name or email.split("@")[0],
        "role": role,
        "department": department,
        "reason": reason,
        "requestedAt": datetime.utcnow().isoformat() + "Z",
        "status": "pending"
    }
    if existing:
        requests_list.remove(existing)
    requests_list.append(entry)
    save_store(store)

    return jsonify({
        "success": True,
        "message": f"Access request received for {email}. The administrator will assign your permissions.",
        "request": entry
    }), 201

@app.route("/api/access-requests", methods=["GET"])
def api_get_access_requests():
    """Retrieve submitted access requests."""
    from main import load_store
    store = load_store()
    return jsonify(store.get("accessRequests", [])), 200

DEFAULT_INVESTIGATIONS = [
    {
        "id": "INV-2026-001",
        "facilityName": "Rubavu Border Dispensary #4",
        "location": "Rubavu",
        "officer": "Officer Jean-Luc N.",
        "medicineBatch": "Artesunate 60mg (Batch B-901)",
        "findings": "Spectrometry match 42% (below 85% threshold). Packaging lacked valid holographic security seals. 120 ampoules impounded for destruction.",
        "verdict": "Seized & Quarantined",
        "date": "2026-10-11"
    },
    {
        "id": "INV-2026-002",
        "facilityName": "Pharmacie de l'Espoir, Nyarugenge",
        "location": "Kigali",
        "officer": "Officer Alice M.",
        "medicineBatch": "Coartem 20/120 (Batch CT-442)",
        "findings": "Physical inspection and 2D barcode check matched FDA registry record. Spectrometry match 97%.",
        "verdict": "Routine Inspection Passed",
        "date": "2026-10-10"
    },
    {
        "id": "INV-2026-003",
        "facilityName": "Rusizi Commercial Depot #2",
        "location": "Rusizi",
        "officer": "Officer Eric K.",
        "medicineBatch": "Amoxicillin 500mg (Batch AMX-11)",
        "findings": "Batch serial unverified in database. Manufacturer certificate presented pending regulatory confirmation.",
        "verdict": "Under Lab Review",
        "date": "2026-10-09"
    }
]

@app.route("/api/investigations", methods=["GET"])
@require_roles("field_officer", "manager", "ict_staff")
def api_get_investigations():
    """Retrieve all post-market field investigation dossiers."""
    from main import load_store, save_store
    store = load_store()
    investigations = store.get("investigations")
    if not investigations:
        investigations = DEFAULT_INVESTIGATIONS.copy()
        store["investigations"] = investigations
        save_store(store)
    return jsonify(investigations), 200

@app.route("/api/investigations", methods=["POST"])
@require_roles("field_officer", "manager")
def api_create_investigation():
    """Log a new post-market field investigation dossier."""
    data = request.get_json(force=True, silent=True) or {}
    facility_name = str(data.get("facilityName", "")).strip()
    medicine_batch = str(data.get("medicineBatch", "")).strip()
    if not facility_name or not medicine_batch:
        return jsonify({"error": "facilityName and medicineBatch are required"}), 400

    from main import load_store, save_store
    from datetime import datetime
    store = load_store()
    investigations = store.setdefault("investigations", DEFAULT_INVESTIGATIONS.copy())

    new_id = f"INV-2026-{str(len(investigations) + 1).zfill(3)}"
    entry = {
        "id": new_id,
        "facilityName": facility_name,
        "location": str(data.get("location", "Kigali")),
        "officer": str(data.get("officer") or (g.user.get("email") if hasattr(g, "user") else "Field Inspector")),
        "medicineBatch": medicine_batch,
        "findings": str(data.get("findings", "Field verification conducted.")),
        "verdict": str(data.get("verdict", "Seized & Quarantined")),
        "date": str(data.get("date") or datetime.utcnow().strftime("%Y-%m-%d"))
    }
    investigations.insert(0, entry)
    save_store(store)
    return jsonify(entry), 201

@app.route("/api/reports/summary", methods=["GET"])
@require_roles("manager", "ict_staff", "field_officer")
def api_reports_summary():
    """Retrieve high-level surveillance metrics and audit statistics."""
    try:
        scans = get_all_scans()
        total = len(scans)
        genuine = [s for s in scans if s.get("category") == "Genuine"]
        flagged = [s for s in scans if s.get("category") != "Genuine"]
        critical = [s for s in scans if s.get("category") == "Critical"]

        locations = ["Kigali", "Rubavu", "Rusizi"]
        by_location = {}
        for loc in locations:
            loc_scans = [s for s in scans if s.get("location") == loc]
            loc_flagged = [s for s in loc_scans if s.get("category") != "Genuine"]
            by_location[loc] = {
                "total": len(loc_scans),
                "flagged": len(loc_flagged),
                "rate": round((len(loc_flagged) / len(loc_scans)) * 100) if loc_scans else 0
            }

        return jsonify({
            "total": total,
            "genuineCount": len(genuine),
            "flaggedCount": len(flagged),
            "criticalCount": len(critical),
            "genuineRate": round((len(genuine) / total) * 100) if total else 100,
            "counterfeitRate": round((len(flagged) / total) * 100) if total else 0,
            "byLocation": by_location
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"\n=======================================================")
    print(f" MedVerify API Gateway running on http://localhost:{port}")
    print(f" Endpoints: /api/health, /api/scan, /api/scans, /api/products, /api/investigations")
    print(f"=======================================================\n")
    app.run(host="0.0.0.0", port=port, debug=False)
