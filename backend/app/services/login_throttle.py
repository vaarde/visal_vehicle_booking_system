"""
Brute-force protection for /auth/login.

Counts failed attempts per (staff_id, client IP) inside a sliding window and refuses further
attempts once the limit is reached. State is kept in process memory: it needs no new dependency
or table, but each gunicorn worker keeps its own count, so the real limit is
LOGIN_MAX_ATTEMPTS x number of workers. A successful login clears the counter.
"""

import threading
import time

from flask import current_app

from ..exceptions import TooManyRequestsError

_lock = threading.Lock()
_failures = {}  # key -> list[float] of failure timestamps


def _key(staff_id, ip):
    return f"{staff_id.lower()}|{ip}"


def _recent(key, now):
    window = current_app.config["LOGIN_WINDOW_SECONDS"]
    recent = [t for t in _failures.get(key, []) if now - t < window]
    if recent:
        _failures[key] = recent
    else:
        _failures.pop(key, None)
    return recent


def ensure_not_locked(staff_id, ip):
    """Raise TooManyRequestsError if this staff_id/IP has used up its attempts."""
    now = time.time()
    key = _key(staff_id, ip)
    with _lock:
        recent = _recent(key, now)
        if len(recent) >= current_app.config["LOGIN_MAX_ATTEMPTS"]:
            wait_minutes = max(1, int((current_app.config["LOGIN_WINDOW_SECONDS"] - (now - recent[0])) // 60) + 1)
            raise TooManyRequestsError(
                f"Too many failed sign-in attempts. Try again in about {wait_minutes} minute(s)."
            )


def record_failure(staff_id, ip):
    with _lock:
        _failures.setdefault(_key(staff_id, ip), []).append(time.time())


def reset(staff_id, ip):
    with _lock:
        _failures.pop(_key(staff_id, ip), None)


def clear_all():
    """Test helper."""
    with _lock:
        _failures.clear()
