import base64
import io
import math
from datetime import datetime

import qrcode
from flask import Blueprint, jsonify, render_template, request
from geopy.distance import geodesic

from .config import (
    ALLOWED_EMAILS,
    CLASSROOM_LAT,
    CLASSROOM_LON,
    MAX_ALLOWED_RADIUS_METERS,
    MAX_LECTURER_ACCURACY_METERS,
    RENDER_URL,
    STUDENT_ACCURACY_REVIEW_THRESHOLD_METERS,
)
from .database import get_db
from .security import generate_token, is_token_valid, lecturer_required


bp = Blueprint("attendance", __name__)


def get_classroom_location():
    row = get_db().execute(
        "SELECT latitude, longitude, accuracy, updated_at FROM classroom_location WHERE id = 1"
    ).fetchone()
    if row:
        return {
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "accuracy": row["accuracy"],
            "updated_at": row["updated_at"],
            "source": "lecturer",
        }
    return {
        "latitude": CLASSROOM_LAT,
        "longitude": CLASSROOM_LON,
        "accuracy": 0.0,
        "updated_at": None,
        "source": "default",
    }


@bp.route("/")
def index():
    return render_template("student.html", token=request.args.get("token", ""))


@bp.route("/lecturer")
def lecturer():
    return render_template("lecturer.html")


@bp.route("/api/classroom-location", methods=["GET", "POST"])
@lecturer_required
def classroom_location():
    if request.method == "GET":
        return jsonify(get_classroom_location())

    data = request.json or {}
    try:
        lat = float(data.get("latitude"))
        lon = float(data.get("longitude"))
        accuracy = float(data.get("accuracy"))
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid coordinates received."}), 400
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and accuracy >= 0):
        return jsonify({"success": False, "message": "Coordinates out of range."}), 400
    if accuracy > MAX_LECTURER_ACCURACY_METERS:
        return jsonify({
            "success": False,
            "message": (
                f"Laptop location is too imprecise (±{accuracy:.0f}m, "
                f"max {int(MAX_LECTURER_ACCURACY_METERS)}m). "
                "Make sure Wi-Fi and Windows location services are on. "
                "Keeping the previous classroom location."
            ),
        }), 422

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO classroom_location "
        "(id, latitude, longitude, accuracy, updated_at) VALUES (1, ?, ?, ?, ?)",
        (lat, lon, accuracy, now),
    )
    db.commit()
    return jsonify({"success": True, **get_classroom_location()})


@bp.route("/api/qr-code")
@lecturer_required
def get_qr_code():
    token = generate_token()
    target_url = f"{RENDER_URL}/?token={token}"
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(target_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    img_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return jsonify({"qr_image": f"data:image/png;base64,{img_str}"})


@bp.route("/api/submit-attendance", methods=["POST"])
def submit_attendance():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    token = data.get("token")

    if not is_token_valid(token):
        return jsonify({"success": False, "message": "Session expired. Please scan the QR again."}), 400
    if not email or data.get("latitude") is None or data.get("longitude") is None:
        return jsonify({"success": False, "message": "Missing email or location."}), 400
    if email not in ALLOWED_EMAILS:
        return jsonify({"success": False, "message": "Email not found in class list."}), 403

    today = datetime.now().strftime("%Y-%m-%d")
    db = get_db()
    existing = db.execute(
        "SELECT id FROM attendance WHERE email = ? AND date = ?", (email, today)
    ).fetchone()
    if existing:
        return jsonify({"success": False, "message": "Already marked attendance today."}), 400

    accuracy_missing = data.get("accuracy") is None
    try:
        lat = float(data.get("latitude"))
        lon = float(data.get("longitude"))
        accuracy = 0.0 if accuracy_missing else float(data.get("accuracy"))
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid coordinates received."}), 400
    if not all(math.isfinite(value) for value in (lat, lon, accuracy)):
        return jsonify({"success": False, "message": "Invalid coordinates received."}), 400
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and accuracy >= 0):
        return jsonify({"success": False, "message": "Coordinates out of range."}), 400

    classroom = get_classroom_location()
    distance_meters = geodesic(
        (classroom["latitude"], classroom["longitude"]), (lat, lon)
    ).meters
    effective_distance = max(0.0, distance_meters - (classroom["accuracy"] / 2))
    client_accuracy_distance = max(
        0.0,
        distance_meters - (accuracy / 2) - (classroom["accuracy"] / 2),
    )

    review_reasons = []
    if accuracy_missing:
        review_reasons.append("The device did not provide an accuracy estimate.")
    elif accuracy > STUDENT_ACCURACY_REVIEW_THRESHOLD_METERS:
        review_reasons.append(
            f"Reported GPS accuracy is low confidence (±{accuracy:.0f}m)."
        )
    if effective_distance > MAX_ALLOWED_RADIUS_METERS:
        if client_accuracy_distance <= MAX_ALLOWED_RADIUS_METERS:
            review_reasons.append(
                "The submitted accuracy estimate is needed to place this location "
                "inside the allowed radius."
            )
        else:
            acc_text = f" (reported accuracy: ±{accuracy:.0f}m)" if accuracy > 0 else ""
            return jsonify({
                "success": False,
                "message": (
                    f"Too far from classroom ({distance_meters:.0f}m away{acc_text}). "
                    f"Max allowed is {int(MAX_ALLOWED_RADIUS_METERS)}m."
                ),
            }), 403

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    review_required = bool(review_reasons)
    review_reason = "; ".join(review_reasons) if review_required else None
    db.execute(
        "INSERT INTO attendance "
        "(email, timestamp, date, distance, review_required, review_reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            email,
            now,
            today,
            round(distance_meters, 1),
            int(review_required),
            review_reason,
        ),
    )
    db.commit()
    message = (
        "Attendance submitted for lecturer review."
        if review_required
        else f"Attendance recorded! ({distance_meters:.0f}m away)"
    )
    return jsonify({
        "success": True,
        "review_required": review_required,
        "message": message,
    })


@bp.route("/api/attendance-list")
@lecturer_required
def attendance_list():
    today = datetime.now().strftime("%Y-%m-%d")
    db = get_db()
    rows = db.execute(
        "SELECT email, timestamp, distance, review_required, review_reason FROM attendance "
        "WHERE date = ? ORDER BY timestamp DESC",
        (today,),
    ).fetchall()
    return jsonify([
        {
            "email": row["email"],
            "time": row["timestamp"].split(" ")[1],
            "distance": row["distance"],
            "review_required": bool(row["review_required"]),
            "review_reason": row["review_reason"],
        }
        for row in rows
    ])
