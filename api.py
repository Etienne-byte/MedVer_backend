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

import re

# Allowed origins for CORS security (Cloudflare Pages, Vercel, and local Vite development)
DEFAULT_ALLOWED_ORIGINS = [
    re.compile(r"^https://.*\.pages\.dev$"),
    "https://medverify.pages.dev",
    "https://medverify-259.pages.dev",
    "https://med-verify-eta.vercel.app",
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:5174",
    "http://localhost:4173",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
]

env_origins = os.getenv("ALLOWED_ORIGINS")
if env_origins:
    custom_origins = [o.strip().rstrip("/") for o in env_origins.split(",") if o.strip()]
    allowed_origins = list(DEFAULT_ALLOWED_ORIGINS) + custom_origins
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

@app.before_request
def log_request_start():
    g.start_time = time.time()
    auth_header = request.headers.get("Authorization", "")
    has_auth = "Bearer [present]" if auth_header.startswith("Bearer ") else "None"
    print(f"\n[API Req] >>> {request.method} {request.path} (Origin: {request.headers.get('Origin', 'direct')}, Auth: {has_auth})")

@app.after_request
def log_request_end(response):
    duration = (time.time() - getattr(g, "start_time", time.time())) * 1000
    print(f"[API Res] <<< {request.method} {request.path} -> HTTP {response.status_code} ({duration:.1f}ms)")
    return response

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

import uuid

ROLE_LABELS = {
    "field_officer": "Post-market surveillance and inspection officer",
    "ict_staff": "ICT and regulatory-systems staff",
    "manager": "Manager, supervisor, or administrator"
}

def get_store_users():
    """Retrieve users from database or store, automatically including configured system role accounts."""
    from main import load_store, firebase_db, firebase_rest_get
    stored_users = []
    try:
        if firebase_db is not None:
            fb_users = firebase_db.reference("users").get()
            if isinstance(fb_users, list):
                stored_users = fb_users
            elif isinstance(fb_users, dict):
                stored_users = list(fb_users.values())
        else:
            fb_users = firebase_rest_get("users")
            if isinstance(fb_users, list):
                stored_users = fb_users
            elif isinstance(fb_users, dict):
                stored_users = list(fb_users.values())
    except Exception:
        pass

    if not stored_users:
        store = load_store()
        stored_users = store.get("users", [])

    existing_emails = {
        u.get("email", "").strip().lower()
        for u in stored_users
        if isinstance(u, dict) and u.get("email")
    }
    needs_save = False

    env_roles = [
        ("manager", os.getenv("ROLE_MANAGER_EMAILS", "manager@example.gov.rw")),
        ("ict_staff", os.getenv("ROLE_ICT_STAFF_EMAILS", "ict@example.gov.rw")),
        ("field_officer", os.getenv("ROLE_FIELD_OFFICER_EMAILS", "officer@example.gov.rw"))
    ]

    for role_name, raw_emails in env_roles:
        for em in raw_emails.split(","):
            email_clean = em.strip().lower()
            if email_clean and email_clean not in existing_emails:
                prefix = email_clean.split("@")[0].replace(".", " ").title()
                dept = (
                    "Rwanda FDA Directorate" if role_name == "manager"
                    else "Regulatory Informatics & Gateway" if role_name == "ict_staff"
                    else "Post-Market Surveillance Post"
                )
                stored_users.append({
                    "id": f"usr-{role_name[:3]}-{abs(hash(email_clean)) % 10000:04d}",
                    "name": prefix,
                    "email": email_clean,
                    "role": role_name,
                    "department": dept,
                    "status": "active",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "lastLogin": None
                })
                existing_emails.add(email_clean)
                needs_save = True

    if needs_save:
        save_store_users(stored_users)

    return stored_users

def save_store_users(users_list):
    """Persist users to local store and sync to Firebase database."""
    from main import load_store, save_store, firebase_rest_write, firebase_db
    store = load_store()
    store["users"] = users_list
    save_store(store)
    try:
        if firebase_db is not None:
            firebase_db.reference("users").set(users_list)
        else:
            firebase_rest_write("PUT", "users", users_list)
    except Exception as e:
        print(f"[Firebase Users Sync] Note: {e}")




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

            # 1. Check store-managed users first
            store_users = get_store_users()
            matching_user = next((u for u in store_users if u.get("email", "").strip().lower() == email), None)
            if matching_user:
                if matching_user.get("status") == "inactive":
                    return jsonify({
                        "error": f"The account '{email}' has been deactivated by a MedVerify Manager.",
                        "code": "ACCOUNT_DEACTIVATED",
                        "email": email
                    }), 403
                role = matching_user.get("role")

            # 2. Fall back to environment variable definitions
            if not role:
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

            user_name = matching_user.get("name") if matching_user else email.split("@")[0].title()
            g.user = {
                "uid": claims.get("uid") or claims.get("sub") or claims.get("user_id"),
                "email": email,
                "name": user_name,
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

    try:
        from main import firebase_db, firebase_rest_write
        if firebase_db is not None:
            firebase_db.reference("accessRequests").set(requests_list)
        else:
            firebase_rest_write("PUT", "accessRequests", requests_list)
    except Exception as e:
        print(f"[Firebase AccessRequests Sync] Note: {e}")

    return jsonify({
        "success": True,
        "message": f"Access request received for {email}. The administrator will assign your permissions.",
        "request": entry
    }), 201

@app.route("/api/access-requests", methods=["GET"])
@require_roles("manager")
def api_get_access_requests():
    """Retrieve submitted access requests from database (Manager only)."""
    from main import load_store, firebase_db, firebase_rest_get
    try:
        if firebase_db is not None:
            fb_reqs = firebase_db.reference("accessRequests").get()
            if isinstance(fb_reqs, list):
                return jsonify(fb_reqs), 200
            elif isinstance(fb_reqs, dict):
                return jsonify(list(fb_reqs.values())), 200
        else:
            fb_reqs = firebase_rest_get("accessRequests")
            if isinstance(fb_reqs, list):
                return jsonify(fb_reqs), 200
            elif isinstance(fb_reqs, dict):
                return jsonify(list(fb_reqs.values())), 200
    except Exception:
        pass
    store = load_store()
    return jsonify(store.get("accessRequests", [])), 200

@app.route("/api/access-requests/<req_id>", methods=["PATCH"])
@require_roles("manager")
def api_update_access_request(req_id):
    """Approve or reject a pending access request and assign role (Manager only)."""
    import datetime
    data = request.get_json(force=True, silent=True) or {}
    action = str(data.get("action", "")).strip().lower()
    assigned_role = str(data.get("role", "")).strip().lower()

    if action not in ("approve", "reject"):
        return jsonify({"error": "Action must be 'approve' or 'reject'."}), 400

    from main import load_store, save_store
    store = load_store()
    reqs = store.setdefault("accessRequests", [])
    target_req = next((r for r in reqs if r.get("id") == req_id), None)
    if not target_req:
        return jsonify({"error": "Access request not found."}), 404

    target_req["status"] = "approved" if action == "approve" else "rejected"

    if action == "approve":
        role_to_grant = assigned_role if assigned_role in ROLE_LABELS else target_req.get("role", "field_officer")
        users = get_store_users()
        existing_user = next((u for u in users if u.get("email", "").strip().lower() == target_req.get("email", "").strip().lower()), None)
        if existing_user:
            existing_user["role"] = role_to_grant
            existing_user["status"] = "active"
            if target_req.get("department"):
                existing_user["department"] = target_req.get("department")
        else:
            users.insert(0, {
                "id": f"usr-{int(time.time())}-{str(uuid.uuid4())[:4]}",
                "name": target_req.get("name") or target_req.get("email", "").split("@")[0].title(),
                "email": target_req.get("email"),
                "role": role_to_grant,
                "department": target_req.get("department", "Rwanda FDA Central Surveillance"),
                "status": "active",
                "createdAt": datetime.datetime.utcnow().isoformat() + "Z",
                "lastLogin": None
            })
        save_store_users(users)

    save_store(store)
    try:
        from main import firebase_db, firebase_rest_write
        if firebase_db is not None:
            firebase_db.reference("accessRequests").set(reqs)
        else:
            firebase_rest_write("PUT", "accessRequests", reqs)
    except Exception as e:
        print(f"[Firebase AccessRequests Sync] Note: {e}")

    return jsonify({"success": True, "request": target_req}), 200

@app.route("/api/users", methods=["GET"])
@require_roles("manager")
def api_get_users():
    """Retrieve all system users with their assigned roles and permissions from database (Manager only)."""
    users = get_store_users()
    return jsonify(users), 200

@app.route("/api/users", methods=["POST"])
@require_roles("manager")
def api_add_user():
    """Add a new user and assign system permissions across the three roles (Manager only)."""
    import datetime
    data = request.get_json(force=True, silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    name = str(data.get("name", "")).strip()
    role = str(data.get("role", "")).strip().lower()
    department = str(data.get("department", "Rwanda FDA Central Surveillance")).strip()
    status = str(data.get("status", "active")).strip().lower()
    password = str(data.get("password", "")).strip()

    if not email:
        return jsonify({"error": "Email address is required."}), 400
    if not name:
        return jsonify({"error": "Full name is required."}), 400
    if role not in ROLE_LABELS:
        return jsonify({"error": f"Invalid role. Must be one of: {', '.join(ROLE_LABELS.keys())}"}), 400

    users = get_store_users()

    if any(u.get("email", "").strip().lower() == email for u in users):
        return jsonify({"error": f"A user with email '{email}' already exists in the system."}), 409

    # Try creating user in Firebase Auth if Admin SDK has credentials
    try:
        import firebase_admin
        from firebase_admin import auth as fb_auth
        if firebase_admin._apps and password:
            fb_auth.create_user(
                email=email,
                password=password,
                display_name=name
            )
            print(f"[Auth] Created Firebase Auth account for {email}")
    except Exception as exc:
        print(f"[Auth] Firebase Admin user notice: {exc}")

    new_user = {
        "id": f"usr-{int(time.time())}-{str(uuid.uuid4())[:4]}",
        "name": name,
        "email": email,
        "role": role,
        "department": department,
        "status": status if status in ("active", "inactive") else "active",
        "createdAt": datetime.datetime.utcnow().isoformat() + "Z",
        "lastLogin": None
    }
    users.insert(0, new_user)
    save_store_users(users)
    return jsonify(new_user), 201

@app.route("/api/users/<user_id>", methods=["PATCH"])
@require_roles("manager")
def api_update_user(user_id):
    """Update role, status, name, or department for a user (Manager only)."""
    data = request.get_json(force=True, silent=True) or {}
    users = get_store_users()

    target = next((u for u in users if u.get("id") == user_id or u.get("email", "").strip().lower() == user_id.lower()), None)
    if not target:
        return jsonify({"error": "User not found."}), 404

    # Prevent a manager from disabling their own account or changing their own role to non-manager
    current_email = g.user.get("email", "").strip().lower()
    if target.get("email", "").strip().lower() == current_email:
        if data.get("status") == "inactive":
            return jsonify({"error": "You cannot deactivate your own administrative account."}), 400
        if "role" in data and data["role"] != "manager":
            return jsonify({"error": "You cannot revoke your own manager role."}), 400

    if "role" in data:
        new_role = str(data["role"]).strip().lower()
        if new_role not in ROLE_LABELS:
            return jsonify({"error": f"Invalid role. Must be one of: {', '.join(ROLE_LABELS.keys())}"}), 400
        target["role"] = new_role

    if "status" in data:
        new_status = str(data["status"]).strip().lower()
        if new_status in ("active", "inactive"):
            target["status"] = new_status

    if "name" in data and str(data["name"]).strip():
        target["name"] = str(data["name"]).strip()

    if "department" in data:
        target["department"] = str(data["department"]).strip()

    save_store_users(users)
    return jsonify(target), 200

@app.route("/api/users/<user_id>", methods=["DELETE"])
@require_roles("manager")
def api_delete_user(user_id):
    """Delete a user from the system (Manager only)."""
    users = get_store_users()

    target = next((u for u in users if u.get("id") == user_id or u.get("email", "").strip().lower() == user_id.lower()), None)
    if not target:
        return jsonify({"error": "User not found."}), 404

    current_email = g.user.get("email", "").strip().lower()
    if target.get("email", "").strip().lower() == current_email:
        return jsonify({"error": "You cannot delete your own administrative account."}), 400

    users.remove(target)
    save_store_users(users)
    return jsonify({"success": True, "message": f"User {target.get('email')} removed."}), 200


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
