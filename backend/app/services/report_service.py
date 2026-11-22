"""
Admin reporting: aggregated figures, a paged booking table and a CSV export, all computed on the server
from the same filters (date range, status, department) so the browser no longer downloads every booking.
"""

import csv
import io
from collections import Counter, defaultdict
from datetime import datetime

from .. import db
from ..models.bookings import Booking
from ..models.users import User

STATUSES = ("Approved", "Pending", "Declined", "Cancelled")
HOURLY_FIRST = 6
HOURLY_LAST = 18
TOP_N = 6

CSV_HEADERS = ["Booking Date", "Staff", "Department", "Start", "End", "Duration (h)", "Location", "Purpose", "Status"]


def filtered_query(date_from=None, date_to=None, status=None, department=None):
    query = Booking.query.join(User, Booking.user_id == User.staff_id)
    if date_from:
        query = query.filter(Booking.booking_date >= date_from)
    if date_to:
        query = query.filter(Booking.booking_date <= date_to)
    if status:
        query = query.filter(Booking.status == status)
    if department:
        query = query.filter(User.department == department)
    return query


def duration_hours(booking):
    start = datetime.combine(booking.booking_date, booking.start_time)
    end = datetime.combine(booking.booking_date, booking.end_time)
    return max(0.0, (end - start).total_seconds() / 3600)


def _top(counter):
    if not counter:
        return {"name": "", "count": 0}
    # highest count first; ties broken alphabetically so the answer is stable
    name, count = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[0]
    return {"name": name, "count": count}


def _ranked(counter, limit=TOP_N):
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return [{"name": name, "value": count} for name, count in ranked]


def department_names():
    """Every department that has at least one user - for the filter dropdown, independent of other filters."""
    rows = db.session.query(User.department).filter(User.department.isnot(None)).distinct().all()
    return sorted({row[0] for row in rows if row[0]})


def build_summary(bookings):
    total = len(bookings)
    by_status = Counter(b.status for b in bookings)
    hours = [duration_hours(b) for b in bookings]
    total_hours = sum(hours)

    staff_ids = {b.user_id for b in bookings}
    locations = Counter(b.location or "Not specified" for b in bookings)
    purposes = Counter(b.purpose or "Not specified" for b in bookings)
    departments = Counter((b.user.department if b.user and b.user.department else "Unassigned") for b in bookings)
    start_hours = Counter(b.start_time.hour for b in bookings)

    daily = defaultdict(float)
    for booking, length in zip(bookings, hours):
        daily[booking.booking_date.isoformat()] += length

    backlog_hours = sum(length for b, length in zip(bookings, hours) if b.status == "Pending")
    peak_hour, peak_count = (None, 0)
    if start_hours:
        peak_hour, peak_count = sorted(start_hours.items(), key=lambda item: (-item[1], item[0]))[0]

    approved = by_status.get("Approved", 0)
    declined = by_status.get("Declined", 0)

    return {
        "totals": {
            "total": total,
            "approved": approved,
            "pending": by_status.get("Pending", 0),
            "declined": declined,
            "cancelled": by_status.get("Cancelled", 0),
            "total_hours": round(total_hours, 2),
            "average_duration_hours": round(total_hours / total, 2) if total else 0,
            "approval_rate": round(approved / total * 100, 1) if total else 0,
            "decline_rate": round(declined / total * 100, 1) if total else 0,
            "unique_staff": len(staff_ids),
            "bookings_per_staff": round(total / len(staff_ids), 2) if staff_ids else 0,
            "backlog_hours": round(backlog_hours, 2),
        },
        "top_location": _top(locations),
        "peak_hour": {"hour": peak_hour, "count": peak_count},
        "by_status": [{"status": status, "value": by_status.get(status, 0)} for status in STATUSES],
        "daily_hours": [{"date": day, "hours": round(value, 1)} for day, value in sorted(daily.items())],
        "by_purpose": _ranked(purposes),
        "by_department": _ranked(departments),
        "hourly_demand": [
            {"hour": hour, "count": start_hours.get(hour, 0)} for hour in range(HOURLY_FIRST, HOURLY_LAST + 1)
        ],
    }


def _safe_cell(value):
    """
    Spreadsheet programs run cells that start with = + - @ as formulas, and purposes/locations are free text
    typed by staff. Prefix such values with an apostrophe so an exported report cannot execute anything.
    """
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def build_csv(bookings):
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_ALL)
    writer.writerow(CSV_HEADERS)
    for b in bookings:
        writer.writerow(
            [
                b.booking_date.isoformat(),
                _safe_cell(b.user.full_name if b.user else b.user_id),
                _safe_cell(b.user.department if b.user else ""),
                b.start_time.strftime("%H:%M"),
                b.end_time.strftime("%H:%M"),
                f"{duration_hours(b):.2f}",
                _safe_cell(b.location),
                _safe_cell(b.purpose),
                b.status,
            ]
        )
    return out.getvalue()
