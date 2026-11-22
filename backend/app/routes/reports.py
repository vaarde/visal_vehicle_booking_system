from datetime import date

from flask import Blueprint, Response, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required

from ..exceptions import BadRequestError, ForbiddenError
from ..models.users import Roles
from ..services import booking_service, report_service
from ..services.report_service import STATUSES

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


def _admin_only():
    if get_jwt().get("role") != Roles.ADMIN:
        raise ForbiddenError("Only admins can view reports")


def _filters():
    """?from=YYYY-MM-DD&to=YYYY-MM-DD&status=Approved&department=Finance (all optional)."""
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    status = request.args.get("status") or None
    if status and status not in STATUSES:
        raise BadRequestError(f"status must be one of: {', '.join(STATUSES)}")
    return {
        "date_from": booking_service.parse_date(date_from, "from") if date_from else None,
        "date_to": booking_service.parse_date(date_to, "to") if date_to else None,
        "status": status,
        "department": request.args.get("department") or None,
    }


@reports_bp.route("/summary", methods=["GET"])
@jwt_required()
def summary():
    _admin_only()
    bookings = report_service.filtered_query(**_filters()).all()
    data = report_service.build_summary(bookings)
    data["departments"] = report_service.department_names()
    return jsonify(data), 200


@reports_bp.route("/bookings", methods=["GET"])
@jwt_required()
def booking_rows():
    """The report table: same filters as the summary, newest first, paged."""
    _admin_only()
    query = booking_service.ordered(report_service.filtered_query(**_filters()), newest_first=True)
    page, page_size = booking_service.parse_paging(request.args)
    page, page_size = page or 1, page_size or booking_service.DEFAULT_PAGE_SIZE
    rows, paging = booking_service.run_listing(query, page, page_size)

    return jsonify(
        {
            "bookings": [
                {**booking_service.serialize_booking(b), "duration_hours": round(report_service.duration_hours(b), 2)}
                for b in rows
            ],
            **paging,
        }
    ), 200


@reports_bp.route("/export.csv", methods=["GET"])
@jwt_required()
def export_csv():
    _admin_only()
    query = booking_service.ordered(report_service.filtered_query(**_filters()), newest_first=True)
    filename = f"vehicle-booking-report-{date.today().isoformat()}.csv"
    return Response(
        report_service.build_csv(query.all()),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
