from flask import Blueprint, Response, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from ..exceptions import BadRequestError, ForbiddenError
from ..models.users import Roles
from ..services import staff_import_service

staff_import_bp = Blueprint("staff_import", __name__, url_prefix="/auth/users/import")


def _admin_only():
    if get_jwt().get("role") != Roles.ADMIN:
        raise ForbiddenError("Only admins can import staff")


@staff_import_bp.route("/template", methods=["GET"])
@jwt_required()
def template():
    _admin_only()
    return Response(
        staff_import_service.TEMPLATE_CSV,
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="staff_import_template.csv"'},
    )


@staff_import_bp.route("", methods=["POST"])
@jwt_required()
def import_staff():
    """Multipart upload: `file` (CSV) and optional `dry_run=true` to validate without saving."""
    _admin_only()

    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise BadRequestError("Choose a CSV file to upload")

    dry_run = request.form.get("dry_run", "false").strip().lower() in ("1", "true", "yes")
    result = staff_import_service.import_staff(
        upload.read(staff_import_service.MAX_FILE_BYTES + 1),
        dry_run=dry_run,
        actor_id=get_jwt_identity(),
        actor_name=get_jwt().get("full_name"),
    )
    return jsonify(result), 200
