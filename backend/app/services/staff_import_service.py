"""
Bulk staff import from a CSV file.

Each row is validated on its own (required fields, lengths, email format, role, duplicates inside the
file and against existing staff). Valid rows are created, invalid rows are skipped and reported with the
reason, so one typo does not block the rest. A dry run validates without saving anything.

If a row has no password, a random temporary one is generated and returned once in the response. It is
never written to the audit log.
"""

import csv
import io
import re
import secrets

from sqlalchemy.exc import IntegrityError

from .. import db
from ..exceptions import BadRequestError, ConflictError
from ..models.users import Roles, User
from .audit_service import log_action

MAX_ROWS = 500
MAX_FILE_BYTES = 1024 * 1024
MIN_PASSWORD_LENGTH = 8

REQUIRED_COLUMNS = ("staff_id", "full_name", "email")
OPTIONAL_COLUMNS = ("phone_number", "department", "role", "password")
ALL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS

TEMPLATE_CSV = (
    ",".join(ALL_COLUMNS) + "\n"
    "EMP001,Ama Mensah,ama.mensah@example.com,0241234567,Finance,staff,\n"
    "EMP002,Kofi Boateng,kofi.boateng@example.com,,Operations,admin,\n"
)

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LIMITS = {"staff_id": 20, "full_name": 100, "email": 100, "phone_number": 20, "department": 50}


def _normalise_header(name):
    return re.sub(r"[\s\-]+", "_", (name or "").strip().lower())


def parse_csv(file_bytes):
    """Return a list of (row_number, {column: value}) where row_number matches the spreadsheet (header = 1)."""
    if not file_bytes or not file_bytes.strip():
        raise BadRequestError("The file is empty")
    if len(file_bytes) > MAX_FILE_BYTES:
        raise BadRequestError("The file is too large. The limit is 1 MB.")

    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise BadRequestError("The file must be a UTF-8 CSV. In Excel choose 'CSV UTF-8' when saving.")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise BadRequestError("The file has no header row")

    headers = [_normalise_header(name) for name in reader.fieldnames]
    missing = [column for column in REQUIRED_COLUMNS if column not in headers]
    if missing:
        raise BadRequestError(f"Missing required column(s): {', '.join(missing)}")

    rows = []
    for index, raw in enumerate(reader, start=2):
        values = {_normalise_header(key): (value or "").strip() for key, value in raw.items() if key is not None}
        if not any(values.values()):
            continue  # blank line
        rows.append((index, values))

    if not rows:
        raise BadRequestError("The file has a header but no staff rows")
    if len(rows) > MAX_ROWS:
        raise BadRequestError(f"The file has {len(rows)} rows. The limit is {MAX_ROWS} per import.")
    return rows


def _validate_row(values, seen_ids, seen_emails, existing_ids, existing_emails):
    errors = []

    for column in REQUIRED_COLUMNS:
        if not values.get(column):
            errors.append(f"{column} is required")

    for column, limit in LIMITS.items():
        if len(values.get(column, "")) > limit:
            errors.append(f"{column} is longer than {limit} characters")

    email = values.get("email", "").lower()
    if email and not EMAIL_PATTERN.match(email):
        errors.append("email is not a valid address")

    role = (values.get("role") or Roles.STAFF).lower()
    if role not in Roles.ALL:
        errors.append(f"role must be one of: {', '.join(Roles.ALL)}")

    password = values.get("password", "")
    if password and len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"password must be at least {MIN_PASSWORD_LENGTH} characters")

    staff_id = values.get("staff_id", "")
    if staff_id:
        if staff_id.lower() in existing_ids:
            errors.append("staff_id already exists")
        elif staff_id.lower() in seen_ids:
            errors.append(f"staff_id is repeated in the file (first on row {seen_ids[staff_id.lower()]})")
    if email:
        if email in existing_emails:
            errors.append("email already belongs to another account")
        elif email in seen_emails:
            errors.append(f"email is repeated in the file (first on row {seen_emails[email]})")

    return errors, role


def import_staff(file_bytes, dry_run, actor_id, actor_name):
    rows = parse_csv(file_bytes)

    existing_ids = {sid.lower() for (sid,) in db.session.query(User.staff_id).all()}
    existing_emails = {email.lower() for (email,) in db.session.query(User.email).all()}

    seen_ids, seen_emails = {}, {}
    results, to_create = [], []

    for row_number, values in rows:
        errors, role = _validate_row(values, seen_ids, seen_emails, existing_ids, existing_emails)

        result = {
            "row": row_number,
            "staff_id": values.get("staff_id", ""),
            "full_name": values.get("full_name", ""),
            "email": values.get("email", "").lower(),
            "role": role,
            "status": "error" if errors else "valid",
            "errors": errors,
        }
        results.append(result)

        if errors:
            continue

        # only rows that will really be created count as "seen", so a bad first row does not block a good later one
        seen_ids[values["staff_id"].lower()] = row_number
        seen_emails[values["email"].lower()] = row_number

        password = values.get("password") or ""
        generated = not password
        if generated:
            password = secrets.token_urlsafe(9)
        to_create.append((result, values, role, password, generated))

    created = 0
    if not dry_run and to_create:
        for result, values, role, password, generated in to_create:
            db.session.add(
                User(
                    staff_id=values["staff_id"],
                    full_name=values["full_name"],
                    email=values["email"],
                    password=password,
                    phone_number=values.get("phone_number") or None,
                    department=values.get("department") or None,
                    role=role,
                )
            )
            result["status"] = "created"
            if generated:
                result["temporary_password"] = password
            created += 1

        skipped = len(results) - created
        log_action(
            actor_id=actor_id,
            actor_name=actor_name,
            action="staff_imported",
            target_type="user",
            description=f"Imported {created} staff account(s) from CSV ({skipped} row(s) skipped)",
            commit=False,
        )
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise ConflictError(
                "Another admin added one of these staff at the same time. Please upload the file again."
            )

    return {
        "dry_run": dry_run,
        "total_rows": len(results),
        "valid": len(to_create),
        "created": created,
        "skipped": len(results) - (len(to_create) if dry_run else created),
        "results": results,
    }
