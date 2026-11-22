"""
Scheduled housekeeping, shared by the in-process scheduler thread and the `flask` CLI commands
(so Render cron can run them instead): the daily booking summary email and audit-log retention.
"""

from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import or_, update

from .. import db
from ..models.app_settings import AppSettings
from ..models.audit_log import AuditLog
from .audit_service import log_action
from .email_service import send_daily_booking_summary
from .settings_service import get_settings


def summary_is_due(now=None):
    """True when summaries are enabled, today's send hour has been reached, and none went out today."""
    now = now or datetime.now()
    settings = get_settings()
    if not settings.daily_summary_enabled:
        return False
    if now.hour < settings.daily_summary_hour:
        return False
    return settings.last_summary_sent_on != now.date()


def _claim_summary_day(today):
    """
    Atomically mark today's summary as taken. Several workers (or a worker and a cron job) can all
    see the summary as due at once; the database lets exactly one UPDATE succeed.
    """
    get_settings()  # makes sure the settings row exists
    result = db.session.execute(
        update(AppSettings)
        .where(
            AppSettings.id == 1,
            or_(AppSettings.last_summary_sent_on.is_(None), AppSettings.last_summary_sent_on < today),
        )
        .values(last_summary_sent_on=today)
    )
    db.session.commit()
    return result.rowcount == 1


def run_daily_summary(now=None, force=False):
    """
    Send the daily summary if it is due and nobody else has sent it today.
    Returns one of: "sent", "failed", "not_due", "already_sent". force=True skips the schedule and the
    once-a-day guard (used by `flask send-daily-summary --force`).
    """
    now = now or datetime.now()
    if not force:
        if not summary_is_due(now):
            return "not_due"
        if not _claim_summary_day(now.date()):
            return "already_sent"

    return "sent" if send_daily_booking_summary() else "failed"


def purge_old_audit_logs(now=None):
    """Delete audit entries older than the retention setting. Returns how many were removed."""
    retention_days = get_settings().audit_log_retention_days
    if not retention_days:
        return 0

    cutoff = (now or datetime.utcnow()) - timedelta(days=retention_days)
    removed = AuditLog.query.filter(AuditLog.created_at < cutoff).delete(synchronize_session=False)
    if removed:
        log_action(
            action="audit_log_purged",
            target_type="audit_log",
            description=f"Removed {removed} audit log entries older than {retention_days} days",
            commit=False,
        )
    db.session.commit()
    if removed:
        current_app.logger.info(f"Purged {removed} audit log entries older than {retention_days} days")
    return removed
