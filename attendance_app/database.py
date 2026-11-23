import sqlite3
from flask import current_app, g

from .config import DB_PATH


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DB_PATH"])
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(db_path=DB_PATH):
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            date TEXT NOT NULL,
            distance REAL,
            review_required INTEGER NOT NULL DEFAULT 0,
            review_reason TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS classroom_location (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            accuracy REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    attendance_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(attendance)")
    }
    if "review_required" not in attendance_columns:
        db.execute(
            "ALTER TABLE attendance ADD COLUMN review_required "
            "INTEGER NOT NULL DEFAULT 0"
        )
    if "review_reason" not in attendance_columns:
        db.execute("ALTER TABLE attendance ADD COLUMN review_reason TEXT")
    db.commit()
    db.close()
