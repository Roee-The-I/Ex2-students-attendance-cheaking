import sqlite3
import os
import tempfile
import unittest
from contextlib import closing

from attendance_app import create_app
from attendance_app.config import (
    ALLOWED_EMAILS,
    CLASSROOM_LAT,
    CLASSROOM_LON,
    LECTURER_KEY,
)
from attendance_app.database import init_db
from attendance_app.security import generate_token


class AttendanceReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self.temp_dir.name, "attendance.db")
        self.app = create_app({"TESTING": True, "DB_PATH": db_path})
        self.client = self.app.test_client()
        self.email = next(iter(ALLOWED_EMAILS))
        self.token = generate_token()

    def tearDown(self):
        self.temp_dir.cleanup()

    def submit(self, distance_meters, accuracy):
        return self.client.post(
            "/api/submit-attendance",
            json={
                "email": self.email,
                "latitude": CLASSROOM_LAT + distance_meters / 111_000,
                "longitude": CLASSROOM_LON,
                "accuracy": accuracy,
                "token": self.token,
            },
        )

    def test_in_radius_location_is_recorded_without_review(self):
        response = self.submit(distance_meters=20, accuracy=5)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["review_required"])

    def test_client_accuracy_cannot_auto_approve_out_of_radius_location(self):
        response = self.submit(distance_meters=120, accuracy=1000)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["review_required"])
        self.assertIn("review", response.json["message"].lower())

        listed = self.client.get(
            "/api/attendance-list",
            headers={"X-Lecturer-Key": LECTURER_KEY},
        )
        self.assertTrue(listed.json[0]["review_required"])
        self.assertIn("accuracy estimate", listed.json[0]["review_reason"])

    def test_out_of_radius_without_accuracy_allowance_is_rejected(self):
        response = self.submit(distance_meters=120, accuracy=10)

        self.assertEqual(response.status_code, 403)

    def test_non_finite_coordinates_are_rejected(self):
        response = self.client.post(
            "/api/submit-attendance",
            json={
                "email": self.email,
                "latitude": float("nan"),
                "longitude": CLASSROOM_LON,
                "accuracy": 5,
                "token": self.token,
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_database_adds_review_columns_to_existing_attendance_table(self):
        db_path = os.path.join(self.temp_dir.name, "legacy.db")
        with closing(sqlite3.connect(db_path)) as db:
            db.execute("""
                CREATE TABLE attendance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    date TEXT NOT NULL,
                    distance REAL
                )
            """)

        init_db(db_path)

        with closing(sqlite3.connect(db_path)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(attendance)")}
        self.assertIn("review_required", columns)
        self.assertIn("review_reason", columns)


if __name__ == "__main__":
    unittest.main()
