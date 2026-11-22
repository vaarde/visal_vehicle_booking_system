"""
Booking business rules: validation against the admin settings, the create / approve / decline /
cancel workflow, and the listing queries. Routes in routes/bookings.py only parse the request and
call these functions.
"""

from datetime import datetime, timedelta

from .. import db
from ..exceptions import BadRequestError, ForbiddenError, NotFoundError
from ..models.bookings import Booking
from ..models.notifications import Notification
from ..models.users import Roles, User
from .audit_service import log_action
from .email_service import send_booking_notification
from .settings_service import get_settings

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 25


# ---------------------------------------------------------------------------
# Serialising
# ---------------------------------------------------------------------------


def serialize_booking(booking, show_private=True):
    """
    One JSON shape for every booking endpoint. `show_private` controls the free-text notes and
    the admin's comment, which only the owner and admins may see on the shared schedule.
    """
    user = booking.user
    return {
        "booking_id": booking.id,
        "staff_id": booking.user_id,
        "staff_name": user.full_name if user else None,
        "department": user.department if user else None,
        "booking_date": booking.booking_date.isoformat(),
        "start_time": booking.start_time.strftime("%H:%M"),
        "end_time": booking.end_time.strftime("%H:%M"),
        "location": booking.location,
        "purpose": booking.purpose,
        "notes": booking.notes if show_private else None,
        "status": booking.status,
        "admin_comment": booking.admin_comment if show_private else None,
        "created_at": booking.created_at.isoformat() if booking.created_at else None,
    }


def can_see_private(booking, viewer_id, viewer_role):
    return viewer_role == Roles.ADMIN or booking.user_id == viewer_id


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def parse_date(value, field):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise BadRequestError(f"{field} must be a date in YYYY-MM-DD format")


def validate_new_booking(data, settings=None):
    """Check a booking request against the format rules and the admin's booking settings."""
    required = ("booking_date", "start_time", "end_time", "location", "purpose")
    if not all(data.get(field) for field in required):
        raise BadRequestError("booking_date, start_time, end_time, location, and purpose are required")

    try:
        booking_date = datetime.strptime(data["booking_date"], "%Y-%m-%d").date()
        start_time = datetime.strptime(data["start_time"], "%H:%M").time()
        end_time = datetime.strptime(data["end_time"], "%H:%M").time()
    except ValueError:
        raise BadRequestError("Invalid date or time format")

    if start_time >= end_time:
        raise BadRequestError("start_time must be earlier than end_time")

    settings = settings or get_settings()

    if start_time < settings.booking_start_time or end_time > settings.booking_end_time:
        raise BadRequestError(
            f"Bookings must fall between {settings.booking_start_time.strftime('%H:%M')} "
            f"and {settings.booking_end_time.strftime('%H:%M')}"
        )

    if not settings.allow_weekend_bookings and booking_date.weekday() >= 5:
        raise BadRequestError("Weekend bookings are not allowed")

    if settings.max_booking_duration_minutes is not None:
        duration = datetime.combine(booking_date, end_time) - datetime.combine(booking_date, start_time)
        if duration.seconds // 60 > settings.max_booking_duration_minutes:
            raise BadRequestError(f"Bookings cannot exceed {settings.max_booking_duration_minutes} minutes")

    if settings.min_lead_time_minutes:
        earliest_allowed = datetime.now() + timedelta(minutes=settings.min_lead_time_minutes)
        if datetime.combine(booking_date, start_time) < earliest_allowed:
            raise BadRequestError(f"Bookings require at least {settings.min_lead_time_minutes} minutes advance notice")

    if settings.max_advance_days is not None:
        latest_allowed = datetime.now().date() + timedelta(days=settings.max_advance_days)
        if booking_date > latest_allowed:
            raise BadRequestError(f"Bookings cannot be made more than {settings.max_advance_days} days in advance")

    return {
        "booking_date": booking_date,
        "start_time": start_time,
        "end_time": end_time,
        "location": data["location"],
        "purpose": data["purpose"],
        "notes": data.get("notes"),
    }


def booking_has_ended(booking):
    return datetime.combine(booking.booking_date, booking.end_time) < datetime.now()


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


def _get_booking(booking_id):
    booking = db.session.get(Booking, booking_id)
    if not booking:
        raise NotFoundError("Booking not found")
    return booking


def _notify_admins(booking, title, message, kind):
    for admin in User.query.filter_by(role=Roles.ADMIN).all():
        db.session.add(
            Notification(user_id=admin.staff_id, title=title, message=message, type=kind, booking_id=booking.id)
        )


def create_booking(user_id, data):
    user = db.session.get(User, user_id)
    if not user:
        raise NotFoundError("User does not exist")

    fields = validate_new_booking(data)
    booking = Booking(user_id=user_id, **fields)
    db.session.add(booking)
    db.session.flush()  # assigns booking.id for the audit entry and notifications

    description = f"{user.full_name} requested a booking on {booking.booking_date.isoformat()} for {booking.location}"
    log_action(
        actor_id=user_id,
        actor_name=user.full_name,
        action="booking_created",
        target_type="booking",
        target_id=booking.id,
        description=description,
        commit=False,
    )
    _notify_admins(booking, "New Booking Request", f"{description}.", "submitted")
    db.session.commit()
    return booking


def approve_booking(booking_id, admin_id, admin_name, admin_comment=None):
    booking = _get_booking(booking_id)
    if booking.status != "Pending":
        raise BadRequestError(f"Booking already {booking.status}")
    if booking_has_ended(booking):
        raise BadRequestError(
            "This booking's time has already passed, so it can no longer be approved. Decline it instead."
        )

    booking.status = "Approved"
    booking.admin_comment = admin_comment
    booking.updated_at = datetime.utcnow()

    message = f"Your booking on {booking.booking_date.isoformat()} has been approved. "
    if admin_comment:
        message += f" Comment: {admin_comment}"
    db.session.add(
        Notification(
            user_id=booking.user_id, title="Booking Approved", message=message, type="approved", booking_id=booking.id
        )
    )
    log_action(
        actor_id=admin_id,
        actor_name=admin_name,
        action="booking_approved",
        target_type="booking",
        target_id=booking.id,
        description=f"Approved booking #{booking.id} for {booking.booking_date.isoformat()}"
        + (f" — {admin_comment}" if admin_comment else ""),
        commit=False,
    )
    db.session.commit()

    _email_decision(booking, approved=True, admin_comment=admin_comment)
    return booking


def decline_booking(booking_id, admin_id, admin_name, admin_comment=None):
    booking = _get_booking(booking_id)
    if booking.status != "Pending":
        raise BadRequestError(f"Booking already {booking.status}")

    if get_settings().require_decline_reason and not (admin_comment and admin_comment.strip()):
        raise BadRequestError("A decline reason is required")

    booking.status = "Declined"
    booking.admin_comment = admin_comment
    booking.updated_at = datetime.utcnow()

    message = f"Your booking on {booking.booking_date.isoformat()} has been declined."
    if admin_comment:
        message += f" Reason: {admin_comment}"
    db.session.add(
        Notification(
            user_id=booking.user_id, title="Booking Declined", message=message, type="declined", booking_id=booking.id
        )
    )
    log_action(
        actor_id=admin_id,
        actor_name=admin_name,
        action="booking_declined",
        target_type="booking",
        target_id=booking.id,
        description=f"Declined booking #{booking.id} for {booking.booking_date.isoformat()}"
        + (f" — {admin_comment}" if admin_comment else ""),
        commit=False,
    )
    db.session.commit()

    _email_decision(booking, approved=False, admin_comment=admin_comment)
    return booking


def cancel_booking(booking_id, actor_id, actor_role, actor_name=None):
    booking = _get_booking(booking_id)

    if booking.user_id != actor_id and actor_role != Roles.ADMIN:
        raise ForbiddenError("You can only cancel your own bookings")
    if booking.status not in ("Pending", "Approved"):
        raise BadRequestError(f"Booking already {booking.status}")
    if booking.booking_date < datetime.now().date():
        raise BadRequestError("Past bookings cannot be cancelled")

    booking.status = "Cancelled"
    booking.updated_at = datetime.utcnow()

    owner_name = booking.user.full_name if booking.user else booking.user_id
    date_text = booking.booking_date.isoformat()
    log_action(
        actor_id=actor_id,
        actor_name=actor_name,
        action="booking_cancelled",
        target_type="booking",
        target_id=booking.id,
        description=f"{actor_name or actor_id} cancelled the booking for {date_text} at {booking.location}",
        commit=False,
    )
    _notify_admins(booking, "Booking Cancelled", f"{owner_name} cancelled their booking for {date_text}.", "cancelled")
    if booking.user_id != actor_id:
        # an admin cancelled it on the requester's behalf - tell the requester
        db.session.add(
            Notification(
                user_id=booking.user_id,
                title="Booking Cancelled",
                message=f"Your booking on {date_text} was cancelled by an administrator.",
                type="cancelled",
                booking_id=booking.id,
            )
        )
    db.session.commit()
    return booking


def _email_decision(booking, approved, admin_comment):
    user = db.session.get(User, booking.user_id)
    if not (user and user.email):
        return

    if approved:
        subject = "Booking Approved - Vehicle Booking System"
        body = f"Dear {user.full_name},\n\nYour vehicle booking has been approved!"
        closing = "\n\nPlease ensure you have all necessary documents and follow the booking guidelines."
    else:
        subject = "Booking Declined - Vehicle Booking System"
        body = f"Dear {user.full_name},\n\nUnfortunately, your vehicle booking request has been declined."
        closing = (
            "\n\nIf you have any questions or need to make another booking request, please contact your administrator."
        )

    if admin_comment:
        body += f"\n\nAdmin comment: {admin_comment}"
    body += closing

    details = {
        "purpose": booking.purpose,
        "userName": user.full_name,
        "date": booking.booking_date.isoformat(),
        "startTime": booking.start_time.strftime("%H:%M"),
        "endTime": booking.end_time.strftime("%H:%M"),
        "location": booking.location,
        "adminComment": admin_comment,
    }
    send_booking_notification(user.email, subject, body, is_approved=approved, booking_details=details)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def apply_filters(query, date_from=None, date_to=None, status=None):
    if date_from:
        query = query.filter(Booking.booking_date >= date_from)
    if date_to:
        query = query.filter(Booking.booking_date <= date_to)
    if status:
        query = query.filter(Booking.status == status)
    return query


def parse_paging(args):
    """
    Pagination is opt-in so existing screens (calendar, dashboard) keep receiving everything.
    Returns (page, page_size) or (None, None) when the caller did not ask for pages.
    """
    if args.get("page") is None and args.get("page_size") is None:
        return None, None
    page = max(1, args.get("page", default=1, type=int))
    page_size = min(MAX_PAGE_SIZE, max(1, args.get("page_size", default=DEFAULT_PAGE_SIZE, type=int)))
    return page, page_size


def run_listing(query, page, page_size):
    """Returns (bookings, paging_info). paging_info is {} when not paginated."""
    if page is None:
        return query.all(), {}
    total = query.count()
    bookings = query.offset((page - 1) * page_size).limit(page_size).all()
    total_pages = max(1, -(-total // page_size))
    return bookings, {"page": page, "page_size": page_size, "total_pages": total_pages, "total": total}


def ordered(query, newest_first=False):
    if newest_first:
        return query.order_by(Booking.booking_date.desc(), Booking.start_time.desc())
    return query.order_by(Booking.booking_date, Booking.start_time)
