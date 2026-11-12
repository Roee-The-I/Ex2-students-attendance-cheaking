import time
import io
import base64
import hmac
import hashlib
import csv
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify
import qrcode
from geopy.distance import geodesic

app = Flask(__name__)

# --- SETTINGS ---
CLASSROOM_LAT = 32.1042
CLASSROOM_LON = 35.1748
MAX_ALLOWED_RADIUS_METERS = 80.0
SECRET_SEED = "ARIEL_I2SE_CLASS_KEY"
RENDER_URL = "https://ex2-students-attendance-cheaking.onrender.com"

# Load allowed emails from CSV (only email column)
ALLOWED_EMAILS = set()
try:
    with open("students.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = row.get("דוא\"ל") or row.get("email") or row.get("Email")
            if email:
                ALLOWED_EMAILS.add(email.strip().lower())
except FileNotFoundError:
    # Fallback small list for testing
    ALLOWED_EMAILS = {"test@example.com", "student@ariel.ac.il"}

# In-memory attendance storage
attendance_records = []  # list of dicts

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
    student_lat = data.get('latitude')
    student_lon = data.get('longitude')
    token = data.get('token')
    
    if token != generate_current_token():
        return jsonify({"success": False, "message": "QR expired. Please scan again."}), 400
    
    if not email or student_lat is None or student_lon is None:
        return jsonify({"success": False, "message": "Missing email or location."}), 400
    
    if email not in ALLOWED_EMAILS:
        return jsonify({"success": False, "message": "Email not found in class list."}), 403
    
    # Check if already marked today (simple)
    today = datetime.now().strftime("%Y-%m-%d")
    for rec in attendance_records:
        if rec["email"] == email and rec["date"] == today:
            return jsonify({"success": False, "message": "Already marked attendance today."}), 400
    
    classroom_coords = (CLASSROOM_LAT, CLASSROOM_LON)
    student_coords = (float(student_lat), float(student_lon))
    distance_meters = geodesic(classroom_coords, student_coords).meters
    
    if distance_meters > MAX_ALLOWED_RADIUS_METERS:
        return jsonify({
            "success": False,
            "message": f"Too far from classroom ({distance_meters:.0f}m)."
        }), 403
    
    # Save
    record = {
        "email": email,
        "time": datetime.now().strftime("%H:%M:%S"),
        "date": today,
        "distance": round(distance_meters, 1)
    }
    attendance_records.append(record)
    
    return jsonify({
        "success": True,
        "message": f"Attendance recorded! ({distance_meters:.0f}m)"
    })

@app.route('/api/attendance-list')
def attendance_list():
    return jsonify(attendance_records)

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
    </style>
</head>
<body>
    <div class="box">
        <h3>Classroom Attendance</h3>
        <label>Email:</label>
        <input type="email" id="email" placeholder="your.email@example.com">
        <input type="hidden" id="token" value="{{ token }}">
        <button onclick="checkIn()">Submit Attendance</button>
        <div id="status"></div>
    </div>
    <script>
        function checkIn() {
            const statusDiv = document.getElementById('status');
            const email = document.getElementById('email').value.trim();
            const token = document.getElementById('token').value;
            if (!email) {
                statusDiv.innerText = "Please enter your email.";
                statusDiv.style.color = "red";
                return;
            }
            if (!navigator.geolocation) {
                statusDiv.innerText = "Geolocation not supported.";
                return;
            }
            statusDiv.innerText = "Getting location...";
            statusDiv.style.color = "black";
            navigator.geolocation.getCurrentPosition(
                (position) => {
                    fetch('/api/submit-attendance', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            email: email,
                            latitude: position.coords.latitude,
                            longitude: position.coords.longitude,
                            token: token
                        })
                    })
                    .then(res => res.json())
                    .then(data => {
                        statusDiv.innerText = data.message;
                        statusDiv.style.color = data.success ? "green" : "red";
                    })
                    .catch(() => {
                        statusDiv.innerText = "Network error.";
                        statusDiv.style.color = "red";
                    });
                },
                (error) => {
                    statusDiv.innerText = "Location error: " + error.message;
                    statusDiv.style.color = "red";
                },
                { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
            );
        }
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)