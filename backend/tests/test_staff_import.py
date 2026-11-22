import io

import pytest

from app import db
from app.models.audit_log import AuditLog
from app.models.users import User
from app.services import staff_import_service as service

HEADER = "staff_id,full_name,email,phone_number,department,role,password\n"


def auth(client, staff_id, password="password123"):
    response = client.post("/auth/login", json={"staff_id": staff_id, "password": password})
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def upload(client, csv_text, dry_run=False, headers=None, filename="staff.csv"):
    data = {"file": (io.BytesIO(csv_text.encode("utf-8") if isinstance(csv_text, str) else csv_text), filename)}
    if dry_run:
        data["dry_run"] = "true"
    return client.post(
        "/auth/users/import",
        data=data,
        content_type="multipart/form-data",
        headers=headers or auth(client, "ADMIN001"),
    )


class TestAccess:
    def test_requires_login(self, client):
        assert client.post("/auth/users/import").status_code == 401
        assert client.get("/auth/users/import/template").status_code == 401

    def test_staff_are_refused(self, client, test_user):
        assert upload(client, HEADER + "N1,New,n@example.com,,,,\n", headers=auth(client, "TEST001")).status_code == 403
        assert client.get("/auth/users/import/template", headers=auth(client, "TEST001")).status_code == 403

    def test_no_file_is_a_400(self, client, test_admin):
        response = client.post("/auth/users/import", headers=auth(client, "ADMIN001"))
        assert response.status_code == 400
        assert response.get_json()["error"] == "Choose a CSV file to upload"

    def test_template_download_is_importable(self, client, test_admin):
        response = client.get("/auth/users/import/template", headers=auth(client, "ADMIN001"))
        assert response.status_code == 200
        assert response.mimetype == "text/csv"
        result = upload(client, response.get_data(as_text=True), dry_run=True).get_json()
        assert result["valid"] == 2 and result["skipped"] == 0  # the sample rows pass validation


class TestImport:
    def test_creates_accounts_that_can_log_in(self, client, test_admin):
        csv_text = HEADER + "N1,Ama Mensah,ama@example.com,0241234567,Finance,staff,supersecret1\n"
        body = upload(client, csv_text).get_json()

        assert body["created"] == 1 and body["skipped"] == 0
        assert body["results"][0]["status"] == "created"
        assert "temporary_password" not in body["results"][0]  # the admin supplied it, so nothing to hand back

        user = db.session.get(User, "N1")
        assert (user.department, user.phone_number, user.role) == ("Finance", "0241234567", "staff")
        assert client.post("/auth/login", json={"staff_id": "N1", "password": "supersecret1"}).status_code == 200

    def test_generates_a_temporary_password_when_blank(self, client, test_admin):
        body = upload(client, HEADER + "N1,Ama,ama@example.com,,,,\n").get_json()
        temporary = body["results"][0]["temporary_password"]

        assert len(temporary) >= 12
        assert client.post("/auth/login", json={"staff_id": "N1", "password": temporary}).status_code == 200

    def test_generated_passwords_are_not_audited(self, client, test_admin):
        body = upload(client, HEADER + "N1,Ama,ama@example.com,,,,\n").get_json()
        temporary = body["results"][0]["temporary_password"]

        entry = AuditLog.query.filter_by(action="staff_imported").one()
        assert entry.actor_id == "ADMIN001"
        assert "Imported 1 staff account(s)" in entry.description
        assert temporary not in (entry.description or "")

    def test_dry_run_saves_nothing(self, client, test_admin):
        body = upload(client, HEADER + "N1,Ama,ama@example.com,,,,\n", dry_run=True).get_json()
        assert body["dry_run"] is True
        assert body["valid"] == 1 and body["created"] == 0
        assert body["results"][0]["status"] == "valid"
        assert "temporary_password" not in body["results"][0]
        assert db.session.get(User, "N1") is None
        assert AuditLog.query.filter_by(action="staff_imported").count() == 0

    def test_role_defaults_to_staff_and_admin_is_allowed(self, client, test_admin):
        upload(client, HEADER + "N1,A,a@example.com,,,,\nN2,B,b@example.com,,,ADMIN,\n")
        assert db.session.get(User, "N1").role == "staff"
        assert db.session.get(User, "N2").role == "admin"

    def test_friendly_header_names_and_bom(self, client, test_admin):
        csv_text = "﻿Staff ID,Full Name,Email,Department\nN1,Ama,ama@example.com,Finance\n"
        body = upload(client, csv_text).get_json()
        assert body["created"] == 1
        assert db.session.get(User, "N1").department == "Finance"

    def test_blank_lines_are_ignored(self, client, test_admin):
        body = upload(client, HEADER + "N1,A,a@example.com,,,,\n,,,,,,\n\n").get_json()
        assert body["total_rows"] == 1


class TestRowValidation:
    def errors_for(self, client, row):
        body = upload(client, HEADER + row + "\n", dry_run=True).get_json()
        return body["results"][0]["errors"]

    @pytest.mark.parametrize(
        "row, expected",
        [
            (",Ama,a@example.com,,,,", "staff_id is required"),
            ("N1,,a@example.com,,,,", "full_name is required"),
            ("N1,Ama,,,,,", "email is required"),
            ("N1,Ama,not-an-email,,,,", "email is not a valid address"),
            ("N1,Ama,a@example.com,,,superuser,", "role must be one of: staff, admin"),
            ("N1,Ama,a@example.com,,,,short", "password must be at least 8 characters"),
            ("N" * 21 + ",Ama,a@example.com,,,,", "staff_id is longer than 20 characters"),
            ("N1,Ama,a@example.com," + "9" * 21 + ",,,", "phone_number is longer than 20 characters"),
        ],
    )
    def test_each_rule(self, client, test_admin, row, expected):
        assert expected in self.errors_for(client, row)

    def test_existing_staff_id_and_email_are_rejected(self, client, test_admin, test_user):
        assert "staff_id already exists" in self.errors_for(
            client, "test001,Dup,new@example.com,,,,"
        )  # case-insensitive
        assert "email already belongs to another account" in self.errors_for(client, "N9,Dup,TEST@example.com,,,,")

    def test_duplicates_inside_the_file_point_to_the_first_row(self, client, test_admin):
        csv_text = HEADER + "N1,A,a@example.com,,,,\nN1,B,b@example.com,,,,\nN3,C,a@example.com,,,,\n"
        results = upload(client, csv_text, dry_run=True).get_json()["results"]
        assert results[0]["status"] == "valid"
        assert "staff_id is repeated in the file (first on row 2)" in results[1]["errors"]
        assert "email is repeated in the file (first on row 2)" in results[2]["errors"]

    def test_a_bad_first_row_does_not_block_a_good_later_one(self, client, test_admin):
        csv_text = HEADER + "N1,,a@example.com,,,,\nN1,Ama,a@example.com,,,,\n"
        results = upload(client, csv_text, dry_run=True).get_json()["results"]
        assert [r["status"] for r in results] == ["error", "valid"]

    def test_bad_rows_are_skipped_good_rows_are_created(self, client, test_admin):
        csv_text = HEADER + "N1,Ama,ama@example.com,,,,\nN2,,broken,,,,\nN3,Kofi,kofi@example.com,,,,\n"
        body = upload(client, csv_text).get_json()

        assert (body["created"], body["skipped"], body["total_rows"]) == (2, 1, 3)
        assert [r["status"] for r in body["results"]] == ["created", "error", "created"]
        assert body["results"][1]["row"] == 3  # spreadsheet row number, header is row 1
        assert db.session.get(User, "N1") and db.session.get(User, "N3") and db.session.get(User, "N2") is None


class TestFileProblems:
    def error(self, client, csv_text):
        response = upload(client, csv_text)
        assert response.status_code == 400
        return response.get_json()["error"]

    def test_empty_file(self, client, test_admin):
        assert self.error(client, "") == "The file is empty"

    def test_missing_required_column(self, client, test_admin):
        assert self.error(client, "staff_id,full_name\nN1,Ama\n") == "Missing required column(s): email"

    def test_header_only(self, client, test_admin):
        assert self.error(client, HEADER) == "The file has a header but no staff rows"

    def test_not_utf8(self, client, test_admin):
        assert "UTF-8" in self.error(client, b"staff_id,full_name,email\nN1,\xff\xfe,a@example.com\n")

    def test_too_many_rows(self, client, test_admin):
        rows = "".join(f"N{i},Name {i},n{i}@example.com,,,,\n" for i in range(service.MAX_ROWS + 1))
        assert f"The limit is {service.MAX_ROWS} per import" in self.error(client, HEADER + rows)

    def test_file_too_large(self, client, test_admin):
        assert "too large" in self.error(
            client, HEADER + "N1," + "x" * (service.MAX_FILE_BYTES + 10) + ",a@example.com\n"
        )
