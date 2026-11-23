import os
import csv


CLASSROOM_LAT = 32.1042
CLASSROOM_LON = 35.1748
MAX_ALLOWED_RADIUS_METERS = 80.0
STUDENT_ACCURACY_REVIEW_THRESHOLD_METERS = MAX_ALLOWED_RADIUS_METERS / 2
MAX_LECTURER_ACCURACY_METERS = 300.0
LECTURER_KEY = os.environ.get("LECTURER_KEY", "change-me")
SECRET_SEED = "ARIEL_I2SE_CLASS_KEY"
RENDER_URL = "https://ex2-students-attendance-cheaking.onrender.com"
DB_PATH = "attendance.db"


def load_allowed_emails():
    allowed_emails = set()
    for csv_name in ("students.csv", "courseid_130158_participants.csv"):
        try:
            with open(csv_name, newline="", encoding="utf-8") as csv_file:
                reader = csv.DictReader(csv_file)
                for row in reader:
                    email = (
                        row.get('דוא"ל')
                        or row.get("email")
                        or row.get("Email")
                        or ""
                    ).strip().lower()
                    if email:
                        allowed_emails.add(email)
            if allowed_emails:
                break
        except FileNotFoundError:
            continue

    if not allowed_emails:
        allowed_emails = {"test@example.com", "student@ariel.ac.il"}
    return allowed_emails


ALLOWED_EMAILS = load_allowed_emails()
