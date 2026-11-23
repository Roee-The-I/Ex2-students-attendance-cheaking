import hashlib
import hmac
import time
from functools import wraps

from flask import jsonify, request

from .config import LECTURER_KEY, SECRET_SEED


def lecturer_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        key = request.headers.get("X-Lecturer-Key", "")
        if not hmac.compare_digest(key.encode(), LECTURER_KEY.encode()):
            return jsonify({"success": False, "message": "Invalid lecturer key."}), 401
        return view(*args, **kwargs)
    return wrapper


def generate_token(time_window=None):
    if time_window is None:
        time_window = int(time.time() // 30)
    message = f"{SECRET_SEED}_{time_window}".encode()
    return hmac.new(SECRET_SEED.encode(), message, hashlib.sha256).hexdigest()[:12]


def is_token_valid(token):
    current_window = int(time.time() // 30)
    for i in range(20):
        if token == generate_token(current_window - i):
            return True
    return False
