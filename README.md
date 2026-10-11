# MedVerify Backend

Flask API gateway for the MedVerify dashboard, scanners, and ESP32.

## Connect Firebase Realtime Database

1. In Firebase Console, open the same project as the database URL in `.env.example` and confirm the Realtime Database URL.
2. Create a service account key for the backend in Project settings > Service accounts. Store the downloaded JSON file as `serviceAccountKey.json` in this backend folder. Do not commit or share this private key.
3. Copy `.env.example` to `.env`. Set `FIREBASE_URL` to the exact Realtime Database URL and `FIREBASE_PROJECT_ID` to the project ID. Set `FIREBASE_CRED` to the key filename or an absolute path.
4. Start the API from this folder with `python api.py`. The API loads `.env` and relative credential paths from this folder even when launched from another working directory.
5. Check `http://localhost:8000/api/health`. Confirm Firebase reports `connected: true` and `authenticated: true` before restricting client access.
6. In Firebase Console > Realtime Database > Rules, publish the contents of `database.rules.json`. The dashboard uses the backend API, and Firebase Admin on the backend bypasses these client rules. Do not apply these deny-all client rules until the backend service account is valid and the health endpoint confirms an authenticated connection.

The service-account key is server-only and must remain outside source control. Do not make the database publicly readable/writable to bypass authentication. Without a valid service-account key, the backend reports RTDB authorization failures and falls back to its local `store.json` cache.

## Rwanda FDA roles and accounts

The dashboard uses Firebase Authentication email/password accounts. There is no public sign-up or client-side role selector. In Firebase Console > Authentication, enable Email/Password and create each staff account. Assign each account by email in the backend `.env`, then restart the API:

```dotenv
ROLE_FIELD_OFFICER_EMAILS=officer@example.gov.rw
ROLE_ICT_STAFF_EMAILS=ict@example.gov.rw
ROLE_MANAGER_EMAILS=manager@example.gov.rw
```

Multiple accounts for a role can be comma-separated. These lists are authoritative; the API verifies each Firebase ID token and rejects accounts not assigned a role.

| Role | Access |
| --- | --- |
| `field_officer` | Dashboard, live barcode/QR scanning, review alerts, and update investigation case status. |
| `ict_staff` | Dashboard read access and product registry management, including product status and demo-data maintenance. |
| `manager` | Dashboard and scan reports, case status review, and case priority assignment (`low`, `normal`, `high`, `urgent`). |

All protected API requests require a valid Firebase ID token. The backend role allowlists must be configured before users can enter the dashboard. Keep `.env` and the service-account key out of source control.

## Run

Install dependencies with `python -m pip install -r requirements.txt`, then start the API using `python api.py`. The API listens on port 8000 unless `PORT` is set.
