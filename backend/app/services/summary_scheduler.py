import threading
import time
from datetime import date, datetime

from flask import current_app

from .maintenance_service import purge_old_audit_logs, run_daily_summary

CHECK_INTERVAL_SECONDS = 60


def run_tick(now=None, last_purge_date=None):
    """
    One scheduler pass: send the summary if it is due, and purge old audit entries once a day.
    Returns the date of the last purge so the caller can pass it back in next time.
    """
    now = now or datetime.now()

    status = run_daily_summary(now)
    if status == "sent":
        current_app.logger.info("Daily booking summary sent")
    elif status == "failed":
        current_app.logger.error("Daily booking summary could not be sent")

    if last_purge_date != now.date():
        purge_old_audit_logs()
        last_purge_date = now.date()
    return last_purge_date


def _scheduler_loop(app):
    """
    Wakes every minute instead of sleeping until a fixed time, so a changed summary hour takes
    effect straight away and an app that was asleep at the scheduled time still sends later that day.
    Duplicates across workers are prevented by the database claim in maintenance_service.

    It waits one interval before the first tick, so short-lived processes that merely build the app
    (migrations, `flask` commands) exit before they could ever send anything.
    """
    last_purge_date: date | None = None
    with app.app_context():
        while True:
            time.sleep(CHECK_INTERVAL_SECONDS)
            try:
                last_purge_date = run_tick(last_purge_date=last_purge_date)
            except Exception as exc:
                current_app.logger.error(f"Scheduled maintenance failed: {exc}")


def start_daily_summary_scheduler(app):
    thread = threading.Thread(target=_scheduler_loop, args=(app,), daemon=True)
    thread.start()
