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

## Run

Install dependencies with `python -m pip install -r requirements.txt`, then start the API using `python api.py`. The API listens on port 8000 unless `PORT` is set.
