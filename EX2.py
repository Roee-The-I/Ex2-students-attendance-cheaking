import time
import io
import os
import re
import html as html_lib
import base64
import hmac
import hashlib
import csv
import sqlite3
import tempfile
import threading
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify, g
import qrcode
import requests
from geopy.distance import geodesic
import google.generativeai as genai

app = Flask(__name__)
# Class recordings are sent to the xAI API. Keep uploads under the host limit.
# The speech API itself accepts up to 500 MB when given a direct file URL.
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

# --- SETTINGS ---
CLASSROOM_LAT = 32.1042
CLASSROOM_LON = 35.1748
MAX_ALLOWED_RADIUS_METERS = 80.0
SECRET_SEED = "ARIEL_I2SE_CLASS_KEY"
RENDER_URL = "https://ex2-students-attendance-cheaking.onrender.com"
DB_PATH = "attendance.db"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# In-memory store for video summary jobs: job_id -> {status, summary, error}
summary_jobs = {}
stam=0;
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
    db.execute("""
    CREATE TABLE IF NOT EXISTS polls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT NOT NULL,
        options TEXT NOT NULL,
        created_at TEXT NOT NULL,
        is_active INTEGER DEFAULT 1
    )
""")
    db.execute("""
    CREATE TABLE IF NOT EXISTS poll_votes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        poll_id INTEGER NOT NULL,
        email TEXT NOT NULL,
        choice TEXT NOT NULL,
        voted_at TEXT NOT NULL,
        UNIQUE(poll_id, email)
    )
""")
    db.commit()
    db.close()

init_db()

def generate_token(time_window=None):
    if time_window is None:
        time_window = int(time.time() // 5)
    msg = f"{SECRET_SEED}_{time_window}".encode()
    return hmac.new(SECRET_SEED.encode(), msg, hashlib.sha256).hexdigest()[:12]

def is_token_valid(token):
    """Accept token from current window or 1 previous window (~5-10 seconds)."""
    current_window = int(time.time() // 5)
    # Allow current + 1 previous window (2 * 5s = 10 seconds max grace)
    for i in range(2):
        if token == generate_token(current_window - i):
            return True
    return False

def generate_session_token():
    """Issue a signed session token after the QR token is validated at scan time."""
    timestamp = int(time.time())
    msg = f"{SECRET_SEED}_session_{timestamp}".encode()
    sig = hmac.new(SECRET_SEED.encode(), msg, hashlib.sha256).hexdigest()[:16]
    return f"{timestamp}_{sig}"

def is_session_token_valid(session_token, max_age_seconds=120):
    """Check if session token is valid and not older than 2 minutes."""
    try:
        parts = session_token.split('_', 1)
        if len(parts) != 2:
            return False
        timestamp = int(parts[0])
        sig = parts[1]
        if time.time() - timestamp > max_age_seconds:
            return False
        msg = f"{SECRET_SEED}_session_{timestamp}".encode()
        expected = hmac.new(SECRET_SEED.encode(), msg, hashlib.sha256).hexdigest()[:16]
        return hmac.compare_digest(sig, expected)
    except (ValueError, TypeError):
        return False

@app.route('/')
def home():
    return render_template_string(HOME_HTML)

@app.route('/qr')
def qr_page():
    return render_template_string(LECTURER_HTML)

@app.route('/comments')
def comments():
    return render_template_string(COMMENTS_HTML)

@app.route('/create-poll', methods=['GET', 'POST'])
def create_poll():
    if request.method == 'POST':
        question = request.form.get('question', '').strip()
        options = request.form.get('options', '').strip()
        if question and options:
            db = get_db()
            # כבה סקרים קודמים
            db.execute("UPDATE polls SET is_active = 0")
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                "INSERT INTO polls (question, options, created_at, is_active) VALUES (?, ?, ?, 1)",
                (question, options, now)
            )
            db.commit()
            return render_template_string("<h2>הסקר נוצר בהצלחה!</h2><a href='/poll'>צפה בסקר</a> | <a href='/create-poll'>צור עוד</a>")
    return render_template_string(CREATE_POLL_HTML)

@app.route('/poll', methods=['GET', 'POST'])
def poll():
    db = get_db()
    poll = db.execute("SELECT * FROM polls WHERE is_active = 1 ORDER BY id DESC LIMIT 1").fetchone()
    
    if not poll:
        return "<h2>אין סקר פעיל כרגע</h2><a href='/'>חזרה</a>"
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        choice = request.form.get('choice')
        if email and choice:
            try:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                db.execute(
                    "INSERT INTO poll_votes (poll_id, email, choice, voted_at) VALUES (?, ?, ?, ?)",
                    (poll['id'], email, choice, now)
                )
                db.commit()
                return "<h2>תודה! ההצבעה נרשמה</h2><a href='/poll'>חזרה</a>"
            except sqlite3.IntegrityError:
                return "<h2>כבר הצבעת בסקר הזה</h2><a href='/poll'>חזרה</a>"
    
    options = [opt.strip() for opt in poll['options'].split(',') if opt.strip()]
    return render_template_string(POLL_HTML, question=poll['question'], options=options)
@app.route('/lecturer')
def lecturer():
    return render_template_string(LECTURER_HOME_HTML)
@app.route('/checkin')
def checkin():
    qr_token = request.args.get('token', '')
    if not is_token_valid(qr_token):
        return "<h2 style='text-align:center;margin-top:60px;'>⏰ Session expired. Please scan the QR code again.</h2>", 403
    # QR was valid at scan time → issue a longer-lived session token for form submission
    session_token = generate_session_token()
    return render_template_string(STUDENT_HTML, token=session_token)

@app.route('/api/qr-code')
def get_qr_code():
    token = generate_token()
    target_url = f"https://ex2-students-attendance-cheaking.onrender.com/checkin?token={token}"    
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

    if not is_session_token_valid(token):
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

# --- VIDEO SUMMARY (Gemini) ---

@app.route('/video-summary')
def video_summary_page():
    return render_template_string(VIDEO_SUMMARY_HTML)

def _process_video_job(job_id, filepath, filename):
    """Background thread: upload to Gemini File API, wait, summarize, clean up."""
    try:
        summary_jobs[job_id]["status"] = "uploading"

        # Upload file to Gemini
        video_file = genai.upload_file(path=filepath, display_name=filename)
        summary_jobs[job_id]["status"] = "processing"

        # Poll until Gemini finishes processing the video
        while video_file.state.name == "PROCESSING":
            time.sleep(5)
            video_file = genai.get_file(video_file.name)

        if video_file.state.name == "FAILED":
            summary_jobs[job_id]["status"] = "error"
            summary_jobs[job_id]["error"] = "Gemini failed to process the video file."
            return

        summary_jobs[job_id]["status"] = "summarizing"

        model = genai.GenerativeModel(model_name="gemini-2.0-flash")
        response = model.generate_content(
            [
                video_file,
                (
                    "This is a university class lecture recording. "
                    "Please provide a comprehensive summary in the SAME LANGUAGE the lecturer speaks in the video. "
                    "Include:\n"
                    "1. **Main Topics** – the key subjects covered\n"
                    "2. **Key Points** – important concepts, definitions, or formulas explained\n"
                    "3. **Examples** – any examples, demos, or exercises shown\n"
                    "4. **Action Items** – homework, assignments, or reading mentioned\n"
                    "5. **Timeline** – approximate timestamps for major topic transitions\n\n"
                    "Format the summary in clear markdown."
                ),
            ],
            request_options={"timeout": 600},
        )

        summary_jobs[job_id]["status"] = "done"
        summary_jobs[job_id]["summary"] = response.text

        # Clean up remote file
        try:
            genai.delete_file(video_file.name)
        except Exception:
            pass

    except Exception as exc:
        summary_jobs[job_id]["status"] = "error"
        summary_jobs[job_id]["error"] = str(exc)
    finally:
        # Clean up local temp file
        try:
            os.unlink(filepath)
        except OSError:
            pass

@app.route('/api/summarize-video', methods=['POST'])
def summarize_video():
    if not GEMINI_API_KEY:
        return jsonify({"success": False, "message": "GEMINI_API_KEY not configured on the server."}), 500

    file = request.files.get('video')
    if not file or file.filename == '':
        return jsonify({"success": False, "message": "No video file uploaded."}), 400

    # Save to a temp file so we can process in background
    suffix = os.path.splitext(file.filename)[1] or ".mp4"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    file.save(tmp)
    tmp.close()

    job_id = hashlib.sha256(f"{time.time()}_{file.filename}".encode()).hexdigest()[:12]
    summary_jobs[job_id] = {"status": "queued", "summary": None, "error": None}

    thread = threading.Thread(target=_process_video_job, args=(job_id, tmp.name, file.filename), daemon=True)
    thread.start()

    return jsonify({"success": True, "job_id": job_id})

@app.route('/api/summary-status/<job_id>')
def summary_status(job_id):
    job = summary_jobs.get(job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404
    return jsonify(job)

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
        <p>Updates every 5 seconds</p>
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
        setInterval(refreshQR, 5000);
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
               statusDiv.innerText=data.message;
               statusDiv.style.color=data.success ?"green" : "red";
               if(data.success){
               setTimeout(()=>{window.location.href="/";}, 1500);
               }
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
HOME_HTML = """
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>דף הבית</title>
    <style>
        body { font-family: Arial, sans-serif; background: #eef2f3; padding: 20px; }
        .box { background: white; padding: 30px; border-radius: 12px; max-width: 400px; margin: 40px auto; text-align: center; }
        a.btn { display: block; background: #007bff; color: white; padding: 14px; margin: 10px 0; border-radius: 8px; text-decoration: none; font-weight: bold; }
    </style>
</head>
<body>
    <div class="box">
        <h2>דף הבית</h2>
        <a href="/comments" class="btn">הערות והצעות</a>
        <a href="/poll" class="btn">ענה על סקר</a>
    </div>
</body>
</html>
"""

LECTURER_HOME_HTML = """
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>דף הבית - מרצה</title>
    <style>
        body { font-family: Arial, sans-serif; background: #f4f4f9; margin: 0; padding: 20px; }
        .box { background: white; padding: 30px; border-radius: 12px; max-width: 420px; margin: 40px auto; text-align: center; box-shadow: 0 4px 10px rgba(0,0,0,0.1); }
        a.btn { display: block; background: #007bff; color: white; padding: 14px; margin: 12px 0; border-radius: 8px; text-decoration: none; font-weight: bold; }
        a.btn:hover { background: #0056b3; }
    </style>
</head>
<body>
    <div class="box">
        <h2>דף המרצה</h2>
        <a href="/qr" class="btn">📷 פתח QR לנוכחות</a>
        <a href="/comments" class="btn">📝 הערות והצעות</a>
        <a href="/create-poll" class="btn">צור סקר</a>
        <a href="/video-summary" class="btn">🎬 סיכום הרצאה מוקלטת</a>
    </div>
</body>
</html>
"""
COMMENTS_HTML = """
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>הערות והצעות</title>
    <style>
        body { font-family: Arial, sans-serif; padding: 15px; background: #f4f4f9; }
        .container { max-width: 900px; margin: 0 auto; background: white; padding: 20px; border-radius: 12px; }
        iframe { width: 100%; height: 70vh; border: none; }
        .back { color: #007bff; text-decoration: none; font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <a href="/" class="back">← חזרה לדף הבית</a>
        <h2 style="text-align:center;">הערות והצעות</h2>
        <iframe src="https://padlet.com/roeygt21/padlet-s025lzfo0hjbeadrsntk"></iframe>
    </div>
</body>
</html>
"""

CREATE_POLL_HTML = """
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>יצירת סקר</title>
    <style>
        body { font-family: Arial; background:#f4f4f9; padding:20px; }
        .box { background:white; padding:25px; border-radius:10px; max-width:500px; margin:auto; }
        input, textarea, button { width:100%; padding:10px; margin:8px 0; box-sizing:border-box; }
        button { background:#007bff; color:white; border:none; border-radius:6px; font-weight:bold; }
    </style>
</head>
<body>
    <div class="box">
        <h2>יצירת סקר חדש</h2>
        <form method="POST">
            <label>שאלה:</label>
            <input type="text" name="question" required placeholder="מה דעתכם על...">
            <label>אפשרויות (מופרדות בפסיק):</label>
            <textarea name="options" required placeholder="אפשרות 1, אפשרות 2, אפשרות 3"></textarea>
            <button type="submit">צור סקר</button>
        </form>
        <p><a href="/">חזרה לדף הבית</a></p>
        
    </div>
</body>
</html>
"""

POLL_HTML = """
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>סקר</title>
    <style>
        body { font-family: Arial; background:#eef2f3; padding:20px; }
        .box { background:white; padding:25px; border-radius:10px; max-width:500px; margin:auto; }
        button { background:#007bff; color:white; border:none; padding:10px 20px; border-radius:6px; }
        label { display:block; margin:8px 0; }
    </style>
</head>
<body>
    <div class="box">
        <h2>{{ question }}</h2>
        <form method="POST">
            <label>אימייל:</label>
            <input type="email" name="email" required style="width:100%; padding:8px;">
            {% for opt in options %}
                <label><input type="radio" name="choice" value="{{ opt }}" required> {{ opt }}</label>
            {% endfor %}
            <br><br>
            <button type="submit">שלח תשובה</button>
        </form>
        <p><a href="/">חזרה לדף הבית</a></p>
    </div>
</body>
</html>
"""
VIDEO_SUMMARY_HTML = """
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>סיכום הרצאה מוקלטת</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: Arial, sans-serif; background: #f4f4f9; margin: 0; padding: 20px; }
        .container { max-width: 700px; margin: 30px auto; }
        .card { background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); }
        h2 { margin-top: 0; text-align: center; }
        .upload-area {
            border: 2px dashed #007bff; border-radius: 10px; padding: 40px 20px;
            text-align: center; cursor: pointer; transition: background 0.2s;
            margin: 20px 0; position: relative;
        }
        .upload-area:hover, .upload-area.dragover { background: #e8f0fe; }
        .upload-area input[type="file"] {
            position: absolute; top: 0; left: 0; width: 100%; height: 100%;
            opacity: 0; cursor: pointer;
        }
        .upload-area .icon { font-size: 48px; margin-bottom: 10px; }
        .upload-area .label { font-size: 16px; color: #555; }
        .file-info { background: #f0f0f0; padding: 10px 15px; border-radius: 8px; margin: 10px 0; display: none; }
        .file-info .name { font-weight: bold; }
        .file-info .size { color: #777; font-size: 14px; }
        button.submit-btn {
            display: block; width: 100%; padding: 14px; border: none; border-radius: 8px;
            background: #007bff; color: white; font-size: 16px; font-weight: bold;
            cursor: pointer; margin-top: 15px;
        }
        button.submit-btn:hover { background: #0056b3; }
        button.submit-btn:disabled { background: #aaa; cursor: not-allowed; }
        .progress-section { display: none; margin-top: 20px; }
        .progress-bar-bg { background: #e0e0e0; border-radius: 8px; height: 10px; overflow: hidden; }
        .progress-bar { height: 100%; background: #007bff; border-radius: 8px; transition: width 0.5s; width: 0%; }
        .progress-label { text-align: center; margin-top: 8px; font-size: 14px; color: #555; }
        .result-section { display: none; margin-top: 25px; }
        .result-section .summary-box {
            background: #f9f9f9; border: 1px solid #ddd; border-radius: 10px;
            padding: 20px; line-height: 1.7; direction: ltr; text-align: left;
            max-height: 600px; overflow-y: auto;
        }
        .error-msg { color: #d32f2f; background: #fdecea; padding: 12px; border-radius: 8px; margin-top: 15px; }
        .back-link { display: inline-block; margin-top: 15px; color: #007bff; text-decoration: none; font-weight: bold; }
        .formats { font-size: 13px; color: #888; margin-top: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h2>🎬 סיכום הרצאה מוקלטת</h2>
            <p style="text-align:center; color:#555;">העלו קובץ וידאו של הרצאה מוקלטת (Zoom, Meet, וכו') וקבלו סיכום אוטומטי באמצעות AI</p>

            <div class="upload-area" id="upload-area">
                <input type="file" id="video-file" accept="video/*,.mp4,.mkv,.avi,.mov,.webm,.m4v">
                <div class="icon">📁</div>
                <div class="label">גררו קובץ וידאו לכאן או לחצו לבחירה</div>
                <div class="formats">MP4, MKV, AVI, MOV, WebM – עד 200MB</div>
            </div>

            <div class="file-info" id="file-info">
                <span class="name" id="file-name"></span><br>
                <span class="size" id="file-size"></span>
            </div>

            <button class="submit-btn" id="submit-btn" disabled onclick="uploadVideo()">📤 העלה וסכם</button>

            <div class="progress-section" id="progress-section">
                <div class="progress-bar-bg"><div class="progress-bar" id="progress-bar"></div></div>
                <div class="progress-label" id="progress-label">מתחיל...</div>
            </div>

            <div class="result-section" id="result-section">
                <h3>📝 סיכום ההרצאה:</h3>
                <div class="summary-box" id="summary-box"></div>
            </div>

            <div id="error-box"></div>

            <a href="/lecturer" class="back-link">← חזרה לדף המרצה</a>
        </div>
    </div>
    <script>
        const fileInput = document.getElementById('video-file');
        const uploadArea = document.getElementById('upload-area');
        const fileInfo = document.getElementById('file-info');
        const fileName = document.getElementById('file-name');
        const fileSize = document.getElementById('file-size');
        const submitBtn = document.getElementById('submit-btn');

        uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('dragover'); });
        uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
        uploadArea.addEventListener('drop', () => uploadArea.classList.remove('dragover'));

        fileInput.addEventListener('change', () => {
            const f = fileInput.files[0];
            if (f) {
                fileName.textContent = f.name;
                fileSize.textContent = formatBytes(f.size);
                fileInfo.style.display = 'block';
                submitBtn.disabled = false;
            }
        });

        function formatBytes(b) {
            if (b < 1024) return b + ' B';
            if (b < 1024*1024) return (b/1024).toFixed(1) + ' KB';
            return (b/(1024*1024)).toFixed(1) + ' MB';
        }

        const STATUS_LABELS = {
            queued: '⏳ בתור לעיבוד...',
            uploading: '📤 מעלה לשרת AI...',
            processing: '🔄 מעבד את הוידאו (עלול לקחת מספר דקות)...',
            summarizing: '✍️ כותב סיכום...',
            done: '✅ הסתיים!',
            error: '❌ שגיאה'
        };
        const STATUS_PROGRESS = { queued: 10, uploading: 25, processing: 55, summarizing: 80, done: 100, error: 100 };

        async function uploadVideo() {
            const f = fileInput.files[0];
            if (!f) return;
            submitBtn.disabled = true;
            submitBtn.textContent = '⏳ מעלה...';
            document.getElementById('progress-section').style.display = 'block';
            document.getElementById('result-section').style.display = 'none';
            document.getElementById('error-box').innerHTML = '';
            setProgress(5, '📤 מעלה קובץ לשרת...');

            const formData = new FormData();
            formData.append('video', f);

            try {
                const res = await fetch('/api/summarize-video', { method: 'POST', body: formData });
                const data = await res.json();
                if (!data.success) { showError(data.message); resetBtn(); return; }
                pollStatus(data.job_id);
            } catch (e) { showError('שגיאת רשת. נסו שנית.'); resetBtn(); }
        }

        function pollStatus(jobId) {
            const iv = setInterval(async () => {
                try {
                    const res = await fetch('/api/summary-status/' + jobId);
                    const data = await res.json();
                    setProgress(STATUS_PROGRESS[data.status] || 0, STATUS_LABELS[data.status] || data.status);
                    if (data.status === 'done') {
                        clearInterval(iv);
                        document.getElementById('result-section').style.display = 'block';
                        document.getElementById('summary-box').innerHTML = simpleMarkdown(data.summary || '');
                        resetBtn();
                    } else if (data.status === 'error') {
                        clearInterval(iv);
                        showError(data.error || 'Unknown error');
                        resetBtn();
                    }
                } catch (e) {}
            }, 3000);
        }

        function setProgress(pct, label) {
            document.getElementById('progress-bar').style.width = pct + '%';
            document.getElementById('progress-label').textContent = label;
        }
        function showError(msg) {
            document.getElementById('error-box').innerHTML = '<div class="error-msg">' + msg + '</div>';
        }
        function resetBtn() {
            submitBtn.disabled = false;
            submitBtn.textContent = '📤 העלה וסכם';
        }
        function simpleMarkdown(md) {
            return md
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                .replace(/^### (.+)$/gm, '<h3>$1</h3>')
                .replace(/^## (.+)$/gm, '<h2>$1</h2>')
                .replace(/^# (.+)$/gm, '<h1>$1</h1>')
                .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
                .replace(/\*(.+?)\*/g, '<em>$1</em>')
                .replace(/^- (.+)$/gm, '<li>$1</li>')
                .replace(/\n/g, '<br>');
        }
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)