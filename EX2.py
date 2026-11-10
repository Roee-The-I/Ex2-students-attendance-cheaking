import time
import io
import base64
from flask import Flask, render_template_string, request, jsonify
import qrcode
from geopy.distance import geodesic

app = Flask(__name__)

# --- CLASSROOM LOCATION SETTINGS ---
# Replace these with the exact coordinates of your lecture hall
CLASSROOM_LAT = 32.1042
CLASSROOM_LON = 35.1748
MAX_ALLOWED_RADIUS_METERS = 30.0  # Allowed radius from classroom center

SECRET_SEED = "ARIEL_I2SE_CLASS_KEY"

def generate_current_token():
    """Generates a token valid for 30-second windows."""
    time_window = int(time.time() // 30)
    return str(hash(f"{SECRET_SEED}_{time_window}"))

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
    student_id = data.get('student_id')
    student_lat = data.get('latitude')
    student_lon = data.get('longitude')
    token = data.get('token')
    
    # 1. Validate Time-Based Token
    if token != generate_current_token():
        return jsonify({
            "success": False, 
            "message": "QR code expired! Please scan the screen again."
        }), 400
        
    if not student_id or student_lat is None or student_lon is None:
        return jsonify({
            "success": False, 
            "message": "Missing Student ID or GPS location data."
        }), 400

    # 2. Calculate Distance via Geodesic Formula
    classroom_coords = (CLASSROOM_LAT, CLASSROOM_LON)
    student_coords = (student_lat, student_lon)
    distance_meters = geodesic(classroom_coords, student_coords).meters
    
    # 3. Evaluate Radius Boundary
    if distance_meters <= MAX_ALLOWED_RADIUS_METERS:
        return jsonify({
            "success": True, 
            "message": f"Attendance recorded! (Verified distance: {distance_meters:.1f}m)"
        })
    else:
        return jsonify({
            "success": False, 
            "message": f"Check-in failed. You are {distance_meters:.1f}m away from the classroom."
        }), 403

# Frontend HTML templates (Lecturer display and Student check-in) remain active
# --- HTML TEMPLATES ---

LECTURER_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Lecturer Attendance Screen</title>
    <style>
        body { font-family: Arial, sans-serif; text-align: center; margin-top: 50px; background: #f4f4f9; }
        .card { background: white; padding: 30px; display: inline-block; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        img { width: 300px; height: 300px; }
    </style>
</head>
<body>
    <div class="card">
        <h2>Scan QR to Mark Attendance</h2>
        <p>Code updates automatically every 30 seconds</p>
        <img id="qr-img" src="" alt="QR Code">
    </div>
    <script>
        function refreshQR() {
            fetch('/api/qr-code')
                .then(res => res.json())
                .then(data => {
                    document.getElementById('qr-img').src = data.qr_image;
                });
        }
        refreshQR();
        setInterval(refreshQR, 10000); // Poll every 10 seconds
    </script>
</body>
</html>
"""

STUDENT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Student Attendance Check-In</title>
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
        <label>Student ID:</label>
        <input type="text" id="student_id" placeholder="e.g. 123456789">
        <input type="hidden" id="token" value="{{ token }}">
        <button onclick="checkIn()">Submit Attendance</button>
        <div id="status"></div>
    </div>

    <script>
        function checkIn() {
            const statusDiv = document.getElementById('status');
            const studentId = document.getElementById('student_id').value;
            const token = document.getElementById('token').value;

            if (!studentId) {
                statusDiv.innerText = "Please enter your Student ID.";
                statusDiv.style.color = "red";
                return;
            }

            if (!navigator.geolocation) {
                statusDiv.innerText = "Geolocation is not supported by your browser.";
                return;
            }

            statusDiv.innerText = "Acquiring location...";
            statusDiv.style.color = "black";

            navigator.geolocation.getCurrentPosition(
                (position) => {
                    const lat = position.coords.latitude;
                    const lon = position.coords.longitude;

                    fetch('/api/submit-attendance', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            student_id: studentId,
                            latitude: lat,
                            longitude: lon,
                            token: token
                        })
                    })
                    .then(res => res.json())
                    .then(data => {
                        statusDiv.innerText = data.message;
                        statusDiv.style.color = data.success ? "green" : "red";
                    });
                },
                (error) => {
                    statusDiv.innerText = "Location access denied or unavailable: " + error.message;
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