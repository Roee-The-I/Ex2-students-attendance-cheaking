from app import app


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
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