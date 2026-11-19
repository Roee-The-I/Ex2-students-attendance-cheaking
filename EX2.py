import time
import io
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
CLASSROOM_LAT = 32.1042
CLASSROOM_LON = 35.1748
MAX_ALLOWED_RADIUS_METERS = 80.0
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
    db.commit()
    db.close()

init_db()

def generate_current_token():
    time_window = int(time.time() // 30)
    msg = f"{SECRET_SEED}_{time_window}".encode()
    return hmac.new(SECRET_SEED.encode(), msg, hashlib.sha256).hexdigest()[:12]

@app.route('/')
def index():
    token = request.args.get('token', '')
    return render_template_string(STUDENT_HTML, token=token)

@app.route('/lecturer')
def lecturer():
    return render_template_string(LECTURER_HTML)

@app.route('/api/qr-code')
def get_qr_code():
    token = generate_current_token()
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

    if token != generate_current_token():
        return jsonify({"success": False, "message": "QR expired. Please scan again."}), 400

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

    classroom_coords = (CLASSROOM_LAT, CLASSROOM_LON)
    student_coords = (lat, lon)
    distance_meters = geodesic(classroom_coords, student_coords).meters
    effective_distance = max(0.0, distance_meters - (accuracy / 2))

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
    </style>
</head>
<body>
    <div class="card">
        <h2>Scan QR to Mark Attendance</h2>
        <p>Updates every 30 seconds</p>
        <img id="qr-img" src="" alt="QR Code">
    </div>
    <h3>Today's Attendance</h3>
    <table id="att-table">
        <thead><tr><th>Email</th><th>Time</th><th>Distance (m)</th></tr></thead>
        <tbody></tbody>
    </table>
    <script>
        function refreshQR() {
            fetch('/api/qr-code')
                .then(res => res.json())
                .then(data => { document.getElementById('qr-img').src = data.qr_image; });
        }
        function loadAttendance() {
            fetch('/api/attendance-list')
                .then(res => res.json())
                .then(data => {
                    const tbody = document.querySelector('#att-table tbody');
                    tbody.innerHTML = '';
                    data.forEach(r => {
                        tbody.innerHTML += `<tr><td>${r.email}</td><td>${r.time}</td><td>${r.distance}</td></tr>`;
                    });
                });
        }
        refreshQR();
        loadAttendance();
        setInterval(refreshQR, 10000);
        setInterval(loadAttendance, 5000);
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