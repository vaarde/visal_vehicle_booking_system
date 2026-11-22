from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from .. import db
from ..exceptions import ForbiddenError, NotFoundError
from ..models.bookings import Booking
from ..models.users import Roles, User
from ..services import booking_service as bookings

bookings_bp = Blueprint("bookings", __name__, url_prefix="/bookings")


def _require_admin(message):
    if get_jwt().get("role") != Roles.ADMIN:
        raise ForbiddenError(message)


def _filters_from_request():
    """Optional ?from=YYYY-MM-DD&to=YYYY-MM-DD&status=Approved filters."""
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    return {
        "date_from": bookings.parse_date(date_from, "from") if date_from else None,
        "date_to": bookings.parse_date(date_to, "to") if date_to else None,
        "status": request.args.get("status") or None,
    }


@bookings_bp.route("/create_booking", methods=["POST"])
@jwt_required()
def create_booking():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No input data provided"}), 400

    booking = bookings.create_booking(get_jwt_identity(), data)
    return jsonify({"message": "Booking created successfully", "booking": bookings.serialize_booking(booking)}), 201


@bookings_bp.route("/staff/<staff_id>", methods=["GET"])
@jwt_required()
def get_bookings_by_staff(staff_id):
    """A staff member's own bookings. Staff may only read their own; admins may read anyone's."""
    role = get_jwt().get("role")
    if staff_id != get_jwt_identity() and role != Roles.ADMIN:
        raise ForbiddenError("You can only view your own bookings")

    user = db.session.get(User, staff_id)
    if not user:
        raise NotFoundError("Staff not found")

    query = bookings.apply_filters(Booking.query.filter_by(user_id=staff_id), **_filters_from_request())
    page, page_size = bookings.parse_paging(request.args)
    rows, paging = bookings.run_listing(bookings.ordered(query, newest_first=True), page, page_size)

    return jsonify(
        {
            "staff": {
                "staff_id": user.staff_id,
                "full_name": user.full_name,
                "department": user.department,
                "role": user.role,
            },
            "total_bookings": paging.get("total", len(rows)),
            "bookings": [bookings.serialize_booking(b) for b in rows],
            **paging,
        }
    ), 200


@bookings_bp.route("/schedule_view", methods=["GET"])
@jwt_required()
def get_all_bookings():
    """
    The shared schedule. Everyone signed in can see it, but free-text notes and admin comments on
    other people's bookings are only visible to admins.
    """
    viewer_id = get_jwt_identity()
    viewer_role = get_jwt().get("role")

    query = bookings.apply_filters(Booking.query, **_filters_from_request())
    page, page_size = bookings.parse_paging(request.args)
    rows, paging = bookings.run_listing(bookings.ordered(query), page, page_size)

    return jsonify(
        {
            "total_bookings": paging.get("total", len(rows)),
            "bookings": [
                bookings.serialize_booking(b, show_private=bookings.can_see_private(b, viewer_id, viewer_role))
                for b in rows
            ],
            **paging,
        }
    ), 200


@bookings_bp.route("/pending", methods=["GET"])
@jwt_required()
def get_pending_bookings():
    """Admin approval queue: every booking still waiting for a decision."""
    _require_admin("Only admins can view pending bookings")

    query = Booking.query.filter(Booking.status == "Pending")
    page, page_size = bookings.parse_paging(request.args)
    rows, paging = bookings.run_listing(bookings.ordered(query), page, page_size)

    return jsonify(
        {
            "total_pending": paging.get("total", len(rows)),
            "pending_bookings": [bookings.serialize_booking(b) for b in rows],
            **paging,
        }
    ), 200


@bookings_bp.route("/<int:booking_id>/approve", methods=["PATCH"])
@jwt_required()
def approve_booking(booking_id):
    _require_admin("Only admins can approve bookings")
    comment = (request.get_json(silent=True) or {}).get("admin_comment")

    booking = bookings.approve_booking(booking_id, get_jwt_identity(), get_jwt().get("full_name"), comment)
    return jsonify(
        {"message": "Booking approved successfully", "booking_id": booking.id, "status": booking.status}
    ), 200


@bookings_bp.route("/<int:booking_id>/decline", methods=["PATCH"])
@jwt_required()
def decline_booking(booking_id):
    _require_admin("Only admins can decline bookings")
    comment = (request.get_json(silent=True) or {}).get("admin_comment")

    booking = bookings.decline_booking(booking_id, get_jwt_identity(), get_jwt().get("full_name"), comment)
    return jsonify(
        {"message": "Booking declined successfully", "booking_id": booking.id, "status": booking.status}
    ), 200


@bookings_bp.route("/<int:booking_id>/cancel", methods=["PATCH"])
@jwt_required()
def cancel_booking(booking_id):
    claims = get_jwt()
    booking = bookings.cancel_booking(booking_id, get_jwt_identity(), claims.get("role"), claims.get("full_name"))
    return jsonify(
        {"message": "Booking cancelled successfully", "booking_id": booking.id, "status": booking.status}
    ), 200
