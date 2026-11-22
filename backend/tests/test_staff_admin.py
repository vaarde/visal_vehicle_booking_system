from datetime import datetime, timedelta

import pytest

from app import db
from app.models.audit_log import AuditLog
from app.models.users import User


def login(client, staff_id, password="password123"):
    return client.post("/auth/login", json={"staff_id": staff_id, "password": password})


def auth(client, staff_id, password="password123"):
    response = login(client, staff_id, password)
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


class TestLogin:
    def test_success_returns_token_role_and_audits(self, client, test_user):
        response = login(client, "TEST001")
        body = response.get_json()
        assert response.status_code == 200
        assert body["role"] == "staff" and body["staff_id"] == "TEST001" and body["access_token"]
        assert AuditLog.query.filter_by(action="login", actor_id="TEST001").count() == 1
        assert db.session.get(User, "TEST001").last_active is not None

    def test_wrong_password_and_unknown_user_look_the_same(self, client, test_user):
        wrong = login(client, "TEST001", "nope")
        unknown = login(client, "NOBODY", "password123")
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.get_json() == unknown.get_json() == {"error": "Invalid credentials"}

    def test_deactivated_account_cannot_log_in(self, client, test_user):
        db.session.get(User, "TEST001").status = "inactive"
        db.session.commit()
        response = login(client, "TEST001")
        assert response.status_code == 403
        assert "deactivated" in response.get_json()["error"]

    def test_missing_fields(self, client):
        assert login(client, "", "").status_code == 400
        assert client.post("/auth/login").status_code in (400, 415)

    def test_staff_id_is_trimmed(self, client, test_user):
        assert login(client, "  TEST001  ").status_code == 200


class TestRegister:
    body = {"staff_id": "NEW1", "full_name": "New Person", "email": "New@Example.com", "password": "password123"}

    def test_admin_registers_staff_and_it_is_audited(self, client, test_admin):
        response = client.post("/auth/register", json=self.body, headers=auth(client, "ADMIN001"))
        assert response.status_code == 201
        user = db.session.get(User, "NEW1")
        assert user.email == "new@example.com" and user.role == "staff"  # email lower-cased, role defaults to staff
        entry = AuditLog.query.filter_by(action="staff_registered").one()
        assert entry.actor_id == "ADMIN001" and "Registered staff account for New Person (NEW1)" in entry.description

    def test_staff_cannot_register(self, client, test_user):
        assert client.post("/auth/register", json=self.body, headers=auth(client, "TEST001")).status_code == 403

    @pytest.mark.parametrize(
        "overrides, message",
        [
            ({"staff_id": ""}, "are required"),
            ({"password": ""}, "are required"),
            ({"staff_id": "TEST001"}, "Staff ID already exists"),
            ({"email": "test@example.com"}, "Email already exists"),
        ],
    )
    def test_validation(self, client, test_admin, test_user, overrides, message):
        response = client.post("/auth/register", json={**self.body, **overrides}, headers=auth(client, "ADMIN001"))
        assert response.status_code == 400
        assert message in response.get_json()["error"]


class TestUserList:
    def test_admin_only(self, client, test_user, test_admin):
        assert client.get("/auth/users", headers=auth(client, "TEST001")).status_code == 403
        body = client.get("/auth/users", headers=auth(client, "ADMIN001")).get_json()
        assert body["count"] == 2
        assert [u["staff_id"] for u in body["users"]] == ["ADMIN001", "TEST001"]  # sorted by staff id
        assert "password_hash" not in body["users"][0]


class TestUpdateUser:
    def put(self, client, body, target="TEST001", who="ADMIN001"):
        return client.put(f"/auth/users/{target}", json=body, headers=auth(client, who))

    def test_updates_details_and_audits_what_changed(self, client, test_admin, test_user):
        response = self.put(client, {"full_name": "Renamed", "department": "Finance", "phone_number": "0240000000"})
        assert response.status_code == 200
        user = db.session.get(User, "TEST001")
        assert (user.full_name, user.department, user.phone_number) == ("Renamed", "Finance", "0240000000")

        entry = AuditLog.query.filter_by(action="staff_updated").one()
        assert "name" in entry.description and "department" in entry.description and "phone" in entry.description

    def test_non_admin_is_refused(self, client, test_user, test_admin):
        assert self.put(client, {"full_name": "X"}, who="TEST001").status_code == 403

    def test_unknown_user(self, client, test_admin):
        assert self.put(client, {"full_name": "X"}, target="NOPE").status_code == 404

    def test_email_must_be_unique(self, client, test_admin, test_user):
        response = self.put(client, {"email": "admin@example.com"})
        assert response.status_code == 400
        assert "already in use" in response.get_json()["error"]

    def test_name_and_email_cannot_be_blank(self, client, test_admin, test_user):
        assert self.put(client, {"full_name": ""}).status_code == 400
        assert self.put(client, {"email": ""}).status_code == 400

    def test_admin_cannot_change_their_own_staff_id(self, client, test_admin):
        response = self.put(client, {"staff_id": "ADMIN999"}, target="ADMIN001")
        assert response.status_code == 400
        assert "cannot change your own staff ID" in response.get_json()["error"]

    def test_new_staff_id_must_be_free(self, client, test_admin, test_user):
        response = self.put(client, {"staff_id": "ADMIN001"})
        assert response.status_code == 400
        assert "already in use" in response.get_json()["error"]


class TestAccountStatus:
    def patch(self, client, target, status, who="ADMIN001"):
        return client.patch(f"/auth/users/{target}/status", json={"status": status}, headers=auth(client, who))

    def test_deactivate_and_reactivate_are_audited(self, client, test_admin, test_user):
        assert self.patch(client, "TEST001", "inactive").status_code == 200
        assert db.session.get(User, "TEST001").status == "inactive"
        assert login(client, "TEST001").status_code == 403

        assert self.patch(client, "TEST001", "ACTIVE").status_code == 200  # case-insensitive
        assert login(client, "TEST001").status_code == 200
        actions = [e.action for e in AuditLog.query.order_by(AuditLog.id) if e.action.startswith("staff_")]
        assert actions == ["staff_deactivated", "staff_activated"]

    def test_cannot_change_own_status(self, client, test_admin):
        response = self.patch(client, "ADMIN001", "inactive")
        assert response.status_code == 400

    def test_invalid_status_and_unknown_user(self, client, test_admin, test_user):
        assert self.patch(client, "TEST001", "suspended").status_code == 400
        assert self.patch(client, "NOPE", "inactive").status_code == 404

    def test_staff_cannot_change_status(self, client, test_user, test_admin):
        assert self.patch(client, "ADMIN001", "inactive", who="TEST001").status_code == 403


class TestMyProfile:
    def test_get_me(self, client, test_user):
        user = client.get("/auth/me", headers=auth(client, "TEST001")).get_json()["user"]
        assert user["staff_id"] == "TEST001" and user["email"] == "test@example.com" and user["role"] == "staff"
        assert "password_hash" not in user

    def test_update_name_and_phone_only(self, client, test_user):
        response = client.put(
            "/auth/me",
            json={"full_name": "  Better Name ", "phone_number": "0201112222", "role": "admin", "department": "Hack"},
            headers=auth(client, "TEST001"),
        )
        assert response.status_code == 200
        user = db.session.get(User, "TEST001")
        assert (user.full_name, user.phone_number) == ("Better Name", "0201112222")
        assert (user.role, user.department) == ("staff", "IT")  # role and department stay admin-managed
        assert AuditLog.query.filter_by(action="profile_updated").count() == 1

    def test_name_is_required(self, client, test_user):
        response = client.put("/auth/me", json={"full_name": "  "}, headers=auth(client, "TEST001"))
        assert response.status_code == 400


class TestChangePassword:
    def change(self, client, current, new, who="TEST001"):
        return client.patch(
            "/auth/me/password", json={"current_password": current, "new_password": new}, headers=auth(client, who)
        )

    def test_success_then_old_password_stops_working(self, client, test_user):
        assert self.change(client, "password123", "brand-new-pass").status_code == 200
        assert login(client, "TEST001", "password123").status_code == 401
        assert login(client, "TEST001", "brand-new-pass").status_code == 200
        assert AuditLog.query.filter_by(action="password_changed").count() == 1

    def test_wrong_current_password(self, client, test_user):
        response = self.change(client, "wrong", "brand-new-pass")
        assert response.status_code == 401
        assert response.get_json()["error"] == "Current password is incorrect"

    @pytest.mark.parametrize(
        "current, new, message",
        [
            ("", "brand-new-pass", "are required"),
            ("password123", "", "are required"),
            ("password123", "short", "at least 8 characters"),
            ("password123", "password123", "must be different"),
        ],
    )
    def test_rules(self, client, test_user, current, new, message):
        response = self.change(client, current, new)
        assert response.status_code == 400
        assert message in response.get_json()["error"]


class TestPresence:
    def test_heartbeat_marks_user_online(self, client, test_user, test_admin):
        assert client.post("/auth/heartbeat", headers=auth(client, "TEST001")).status_code == 200
        online = client.get("/auth/online-users", headers=auth(client, "ADMIN001")).get_json()
        assert {u["staff_id"] for u in online["users"]} >= {"TEST001"}

    def test_stale_users_are_not_listed(self, client, test_user, test_admin):
        db.session.get(User, "TEST001").last_active = datetime.utcnow() - timedelta(minutes=30)
        db.session.commit()
        headers = auth(client, "ADMIN001")  # logging in refreshes ADMIN001's own last_active only
        online = client.get("/auth/online-users", headers=headers).get_json()
        assert "TEST001" not in {u["staff_id"] for u in online["users"]}

    def test_requires_login(self, client):
        assert client.post("/auth/heartbeat").status_code == 401
        assert client.get("/auth/online-users").status_code == 401
