import os
import time
import io
from functools import wraps
import base64
import hmac
import hashlib
import csv
import sqlite3
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify, g
import qrcode
from geopy.distance import geodesic

app = Flask(__name__)

# --- SETTINGS ---
# Fallback classroom location, used until the lecturer's laptop reports its own location.
CLASSROOM_LAT = 32.1042
CLASSROOM_LON = 35.1748
MAX_ALLOWED_RADIUS_METERS = 80.0
# Laptop readings less accurate than this (e.g. IP-based guesses) are rejected.
MAX_LECTURER_ACCURACY_METERS = 300.0
# Key the lecturer enters once on /lecturer. Set LECTURER_KEY in the environment (e.g. on Render).
LECTURER_KEY = os.environ.get("LECTURER_KEY", "change-me")
SECRET_SEED = "ARIEL_I2SE_CLASS_KEY"
RENDER_URL = "https://ex2-students-attendance-cheaking.onrender.com"
DB_PATH = "attendance.db"

# Load allowed emails
ALLOWED_EMAILS = set()
csv_files = ["students.csv", "courseid_130158_participants.csv"]
for csv_name in csv_files:
    try:
        with open(csv_name, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                email = (row.get("דוא\"ל") or row.get("email") or row.get("Email") or "").strip().lower()
                if email:
                    ALLOWED_EMAILS.add(email)
        if ALLOWED_EMAILS:
            break
    except FileNotFoundError:
        continue

if not ALLOWED_EMAILS:
    ALLOWED_EMAILS = {"test@example.com", "student@ariel.ac.il"}

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            date TEXT NOT NULL,
            distance REAL
        )
    """)
    # Create a table to store the lecturer's laptop location
    db.execute("""
        CREATE TABLE IF NOT EXISTS classroom_location (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            accuracy REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    db.commit()
    db.close()

init_db()

if LECTURER_KEY == "change-me":
    print("WARNING: LECTURER_KEY is not set; using the default key 'change-me'.")

def lecturer_required(view):
    """Reject requests that don't carry the lecturer key in the X-Lecturer-Key header."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        key = request.headers.get("X-Lecturer-Key", "")
        if not hmac.compare_digest(key.encode(), LECTURER_KEY.encode()):
            return jsonify({"success": False, "message": "Invalid lecturer key."}), 401
        return view(*args, **kwargs)
    return wrapper

def get_classroom_location():
    """Location last reported by the lecturer's laptop, or the fixed fallback."""
    row = get_db().execute(
        "SELECT latitude, longitude, accuracy, updated_at FROM classroom_location WHERE id = 1"
    ).fetchone()
    if row:
        return {"latitude": row["latitude"], "longitude": row["longitude"],
                "accuracy": row["accuracy"], "updated_at": row["updated_at"], "source": "lecturer"}
    return {"latitude": CLASSROOM_LAT, "longitude": CLASSROOM_LON,
            "accuracy": 0.0, "updated_at": None, "source": "default"}

def generate_token(time_window=None):
    if time_window is None:
        time_window = int(time.time() // 30)
    msg = f"{SECRET_SEED}_{time_window}".encode()
    return hmac.new(SECRET_SEED.encode(), msg, hashlib.sha256).hexdigest()[:12]

def is_token_valid(token):
    """Accept token from current window or previous windows (up to 10 minutes)."""
    current_window = int(time.time() // 30)
    # Allow up to 20 windows back (20 * 30s = 10 minutes)
    for i in range(20):
        if token == generate_token(current_window - i):
            return True
    return False

@app.route('/')
def index():
    token = request.args.get('token', '')
    return render_template_string(STUDENT_HTML, token=token)

@app.route('/lecturer')
def lecturer():
    return render_template_string(LECTURER_HTML)

@app.route('/api/classroom-location', methods=['GET', 'POST'])
@lecturer_required
def classroom_location():
    if request.method == 'GET':
        return jsonify(get_classroom_location())

    data = request.json or {}
    try:
        lat = float(data.get('latitude'))
        lon = float(data.get('longitude'))
        accuracy = float(data.get('accuracy'))
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid coordinates received."}), 400
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and accuracy >= 0):
        return jsonify({"success": False, "message": "Coordinates out of range."}), 400
    if accuracy > MAX_LECTURER_ACCURACY_METERS:
        return jsonify({
            "success": False,
            "message": f"Laptop location is too imprecise (±{accuracy:.0f}m, max {int(MAX_LECTURER_ACCURACY_METERS)}m). "
                       "Make sure Wi-Fi and Windows location services are on. Keeping the previous classroom location."
        }), 422

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO classroom_location (id, latitude, longitude, accuracy, updated_at) VALUES (1, ?, ?, ?, ?)",
        (lat, lon, accuracy, now)
    )
    db.commit()
    return jsonify({"success": True, **get_classroom_location()})

@app.route('/api/qr-code')
@lecturer_required
def get_qr_code():
    token = generate_token()
    target_url = f"https://ex2-students-attendance-cheaking.onrender.com/?token={token}"
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(target_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    img_str = base64.b64encode(buffer.getvalue()).decode('utf-8')
    return jsonify({"qr_image": f"data:image/png;base64,{img_str}"})

@app.route('/api/submit-attendance', methods=['POST'])
def submit_attendance():
    data = request.json or {}
    email = (data.get('email') or "").strip().lower()
    token = data.get('token')

    if not is_token_valid(token):
        return jsonify({"success": False, "message": "Session expired. Please scan the QR again."}), 400

    if not email or data.get('latitude') is None or data.get('longitude') is None:
        return jsonify({"success": False, "message": "Missing email or location."}), 400

    if email not in ALLOWED_EMAILS:
        return jsonify({"success": False, "message": "Email not found in class list."}), 403

    today = datetime.now().strftime("%Y-%m-%d")
    db = get_db()
    existing = db.execute(
        "SELECT id FROM attendance WHERE email = ? AND date = ?",
        (email, today)
    ).fetchone()
    if existing:
        return jsonify({"success": False, "message": "Already marked attendance today."}), 400

    try:
        lat = float(data.get('latitude'))
        lon = float(data.get('longitude'))
        accuracy = float(data.get('accuracy', 0))
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid coordinates received."}), 400

    classroom = get_classroom_location()
    classroom_coords = (classroom["latitude"], classroom["longitude"])
    student_coords = (lat, lon)
    distance_meters = geodesic(classroom_coords, student_coords).meters
    # Allow for the uncertainty of both readings (student's phone and lecturer's laptop).
    effective_distance = max(0.0, distance_meters - (accuracy / 2) - (classroom["accuracy"] / 2))

    if effective_distance > MAX_ALLOWED_RADIUS_METERS:
        acc_text = f" (accuracy: ±{accuracy:.0f}m)" if accuracy > 0 else ""
        return jsonify({
            "success": False,
            "message": f"Too far from classroom ({distance_meters:.0f}m away{acc_text}). Max allowed is {int(MAX_ALLOWED_RADIUS_METERS)}m."
        }), 403

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO attendance (email, timestamp, date, distance) VALUES (?, ?, ?, ?)",
        (email, now, today, round(distance_meters, 1))
    )
    db.commit()

    return jsonify({
        "success": True,
        "message": f"Attendance recorded! ({distance_meters:.0f}m away)"
    })

@app.route('/api/attendance-list')
@lecturer_required
def attendance_list():
    today = datetime.now().strftime("%Y-%m-%d")
    db = get_db()
    rows = db.execute(
        "SELECT email, timestamp, distance FROM attendance WHERE date = ? ORDER BY timestamp DESC",
        (today,)
    ).fetchall()
    return jsonify([{"email": r["email"], "time": r["timestamp"].split(" ")[1], "distance": r["distance"]} for r in rows])

# --- HTML TEMPLATES ---
LECTURER_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Lecturer - Attendance</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; text-align: center; margin: 20px; background: #f4f4f9; }
        .card { background: white; padding: 20px; display: inline-block; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        img { width: 280px; height: 280px; }
        table { margin: 20px auto; border-collapse: collapse; width: 90%; max-width: 600px; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background: #007bff; color: white; }
        #loc-status { max-width: 600px; margin: 15px auto; padding: 10px; border-radius: 6px; background: #f8f9fa; border: 1px solid #ddd; font-size: 14px; }
        #login input, #login button { padding: 10px; font-size: 16px; border-radius: 6px; margin: 5px; }
        #login button { background: #007bff; color: white; border: none; cursor: pointer; }
    </style>
</head>
<body>
    <div id="login" class="card" style="display:none">
        <h2>Lecturer Login</h2>
        <input type="password" id="key-input" placeholder="Lecturer key">
        <button onclick="login()">Enter</button>
        <div id="login-error" style="color:red; margin-top:8px"></div>
    </div>
    <div id="main" style="display:none">
        <div class="card">
            <h2>Scan QR to Mark Attendance</h2>
            <p>Updates every 30 seconds</p>
            <img id="qr-img" src="" alt="QR Code">
        </div>
        <div id="loc-status">📍 Getting this laptop's location...</div>
        <h3>Today's Attendance</h3>
        <table id="att-table">
            <thead><tr><th>Email</th><th>Time</th><th>Distance (m)</th></tr></thead>
            <tbody></tbody>
        </table>
    </div>
    <script>
        const KEY_STORAGE = 'lecturerKey';
        let lecturerKey = '';
        try { lecturerKey = localStorage.getItem(KEY_STORAGE) || ''; } catch (e) {}
        let started = false;
        let lastSent = null;  // last laptop location the server accepted

        function api(path, options = {}) {
            options.headers = Object.assign({}, options.headers, { 'X-Lecturer-Key': lecturerKey });
            return fetch(path, options).then(res => {
                if (res.status === 401) { showLogin('Wrong lecturer key.'); throw new Error('unauthorized'); }
                return res;
            });
        }

        function showLogin(error) {
            try { localStorage.removeItem(KEY_STORAGE); } catch (e) {}
            lecturerKey = '';
            document.getElementById('main').style.display = 'none';
            document.getElementById('login').style.display = 'inline-block';
            document.getElementById('login-error').innerText = error || '';
        }

        function login() {
            lecturerKey = document.getElementById('key-input').value.trim();
            checkKey();
        }
        document.getElementById('key-input').addEventListener('keydown', e => { if (e.key === 'Enter') login(); });

        function checkKey() {
            if (!lecturerKey) { showLogin(); return; }
            api('/api/classroom-location')
                .then(res => res.json())
                .then(loc => {
                    try { localStorage.setItem(KEY_STORAGE, lecturerKey); } catch (e) {}
                    document.getElementById('login').style.display = 'none';
                    document.getElementById('main').style.display = 'block';
                    if (loc.source === 'default') {
                        setLoc('📍 Getting this laptop\\'s location... (using the default classroom location for now)', '#333');
                    } else {
                        setLoc(`📍 Updating location... (last set ${loc.updated_at}, ±${Math.round(loc.accuracy)}m)`, '#333');
                    }
                    start();
                })
                .catch(() => {});
        }

        function start() {
            if (started) return;
            started = true;
            refreshQR();
            loadAttendance();
            setInterval(refreshQR, 10000);
            setInterval(loadAttendance, 5000);
            startLocation();
        }

        function refreshQR() {
            api('/api/qr-code')
                .then(res => res.json())
                .then(data => { document.getElementById('qr-img').src = data.qr_image; })
                .catch(() => {});
        }
        function loadAttendance() {
            api('/api/attendance-list')
                .then(res => res.json())
                .then(data => {
                    const tbody = document.querySelector('#att-table tbody');
                    tbody.innerHTML = '';
                    data.forEach(r => {
                        tbody.innerHTML += `<tr><td>${r.email}</td><td>${r.time}</td><td>${r.distance}</td></tr>`;
                    });
                })
                .catch(() => {});
        }

        // --- Classroom location from this laptop ---
        function setLoc(text, color) {
            const el = document.getElementById('loc-status');
            el.innerText = text;
            el.style.color = color;
        }

        function distanceMeters(a, b) {
            const R = 6371000, toRad = d => d * Math.PI / 180;
            const dLat = toRad(b.latitude - a.latitude), dLon = toRad(b.longitude - a.longitude);
            const h = Math.sin(dLat / 2) ** 2 +
                      Math.cos(toRad(a.latitude)) * Math.cos(toRad(b.latitude)) * Math.sin(dLon / 2) ** 2;
            return 2 * R * Math.asin(Math.sqrt(h));
        }

        function startLocation() {
            if (!navigator.geolocation) {
                setLoc('❌ This browser cannot share location. Students are checked against the default classroom location.', 'red');
                return;
            }
            navigator.geolocation.watchPosition(onPosition, onLocationError,
                { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 });
        }

        function onPosition(pos) {
            const reading = {
                latitude: pos.coords.latitude,
                longitude: pos.coords.longitude,
                accuracy: pos.coords.accuracy
            };
            // Only send when the reading is more accurate, or the laptop has clearly moved.
            if (lastSent && reading.accuracy >= lastSent.accuracy && distanceMeters(reading, lastSent) < 50) return;

            api('/api/classroom-location', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(reading)
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    lastSent = reading;
                    setLoc(`✅ Classroom location set from this laptop (±${Math.round(reading.accuracy)}m)`, 'green');
                } else {
                    setLoc('⚠️ ' + data.message, 'orange');
                }
            })
            .catch(() => {});
        }

        function onLocationError(err) {
            if (lastSent) return;  // keep the location we already have
            if (err.code === 1) {
                setLoc('❌ Location permission denied. Allow location for this site and reload. Until then the last saved / default classroom location is used.', 'red');
            } else if (err.code === 2) {
                setLoc('⚠️ Location unavailable. On Windows, turn on Settings → Privacy & security → Location, and make sure Wi-Fi is on.', 'orange');
            } else {
                setLoc('⏳ Still waiting for this laptop\\'s location...', '#333');
            }
        }

        checkKey();
    </script>
</body>
</html>
"""

STUDENT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Student Attendance</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; padding: 20px; background: #eef2f3; }
        .box { background: white; padding: 25px; border-radius: 10px; max-width: 400px; margin: auto; }
        input, button { width: 100%; padding: 12px; margin: 8px 0; box-sizing: border-box; border-radius: 6px; font-size: 16px; }
        button { background: #007bff; color: white; border: none; font-weight: bold; cursor: pointer; }
        #status { font-weight: bold; margin-top: 15px; }
        #loc-status { font-size: 14px; margin: 10px 0; padding: 10px; border-radius: 6px; background: #f8f9fa; border: 1px solid #ddd; }
    </style>
</head>
<body>
    <div class="box">
        <h3>Classroom Attendance</h3>
        <div id="loc-status">📍 Requesting GPS location...</div>
        <label>Email:</label>
        <input type="email" id="email" placeholder="your.email@example.com">
        <input type="hidden" id="token" value="{{ token }}">
        <button onclick="checkIn()">Submit Attendance</button>
        <div id="status"></div>
    </div>
    <script>
        let userLocation = null;
        let retryTimer = null;
        let watchId = null;

        function requestLocation() {
            if (userLocation) return;
            const locStatus = document.getElementById('loc-status');
            if (!navigator.geolocation) {
                locStatus.innerText = "❌ Geolocation is not supported by your browser.";
                locStatus.style.color = "red";
                return;
            }
            locStatus.innerText = "📍 Acquiring GPS location...";
            locStatus.style.color = "#333";

            if (watchId !== null) navigator.geolocation.clearWatch(watchId);

            watchId = navigator.geolocation.watchPosition(
                (pos) => {
                    userLocation = {
                        latitude: pos.coords.latitude,
                        longitude: pos.coords.longitude,
                        accuracy: pos.coords.accuracy
                    };
                    if (watchId !== null) {
                        navigator.geolocation.clearWatch(watchId);
                        watchId = null;
                    }
                    if (retryTimer) {
                        clearTimeout(retryTimer);
                        retryTimer = null;
                    }
                    locStatus.innerText = `✅ Location acquired (Accuracy: ±${Math.round(pos.coords.accuracy)}m)`;
                    locStatus.style.color = "green";
                },
                (err) => {
                    if (userLocation) return;
                    locStatus.style.color = "red";
                    if (err.code === 1) {
                        locStatus.innerText = "❌ Location permission denied. Please allow location access.";
                    } else if (err.code === 2) {
                        locStatus.innerText = "⚠️ Device GPS is disabled. Please turn on location services.";
                        if (!retryTimer) {
                            retryTimer = setTimeout(() => { retryTimer = null; if (!userLocation) requestLocation(); }, 3000);
                        }
                    } else {
                        locStatus.innerText = "⏳ Acquiring GPS signal (retrying...)";
                        if (!retryTimer) {
                            retryTimer = setTimeout(() => { retryTimer = null; if (!userLocation) requestLocation(); }, 3000);
                        }
                    }
                },
                { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
            );
        }

        window.addEventListener('DOMContentLoaded', requestLocation);
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && !userLocation) requestLocation();
        });
        window.addEventListener('focus', () => {
            if (!userLocation) requestLocation();
        });

        function checkIn() {
            const statusDiv = document.getElementById('status');
            const email = document.getElementById('email').value.trim();
            const token = document.getElementById('token').value;

            if (!email) {
                statusDiv.innerText = "Please enter your email.";
                statusDiv.style.color = "red";
                return;
            }
            if (!userLocation) {
                statusDiv.innerText = "Waiting for location fix... Please allow location access.";
                statusDiv.style.color = "orange";
                requestLocation();
                return;
            }

            statusDiv.innerText = "Submitting attendance...";
            statusDiv.style.color = "black";

            fetch('/api/submit-attendance', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    email: email,
                    latitude: userLocation.latitude,
                    longitude: userLocation.longitude,
                    accuracy: userLocation.accuracy,
                    token: token
                })
            })
            .then(res => res.json())
            .then(data => {
                statusDiv.innerText = data.message;
                statusDiv.style.color = data.success ? "green" : "red";
            })
            .catch(() => {
                statusDiv.innerText = "Network error. Please try again.";
                statusDiv.style.color = "red";
            });
        }
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)