import pytest

from app import db
from app.models.audit_log import AuditLog
from app.models.notifications import Notification
from app.models.support_message import SupportMessage
from app.services.settings_service import get_settings


def auth(client, staff_id, password="password123"):
    response = client.post("/auth/login", json={"staff_id": staff_id, "password": password})
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


class TestSettings:
    def put(self, client, body, who="ADMIN001"):
        return client.put("/settings", json=body, headers=auth(client, who))

    def test_requires_login(self, client):
        assert client.get("/settings").status_code == 401
        assert client.put("/settings", json={}).status_code == 401

    def test_everyone_signed_in_can_read_defaults(self, client, test_user):
        settings = client.get("/settings", headers=auth(client, "TEST001")).get_json()["settings"]
        assert settings["booking_start_time"] == "06:00"
        assert settings["booking_end_time"] == "18:00"
        assert settings["require_decline_reason"] is True
        assert settings["audit_log_retention_days"] is None

    def test_staff_cannot_change_settings(self, client, test_user):
        assert self.put(client, {"org_name": "Hacked"}, who="TEST001").status_code == 403

    def test_admin_updates_values_and_it_is_audited(self, client, test_admin):
        response = self.put(
            client,
            {
                "booking_start_time": "07:30",
                "max_booking_duration_minutes": "120",
                "allow_weekend_bookings": False,
                "week_start_day": "Sunday",
                "org_name": "  Visal Ltd  ",
            },
        )
        assert response.status_code == 200
        settings = response.get_json()["settings"]
        assert settings["booking_start_time"] == "07:30"
        assert settings["max_booking_duration_minutes"] == 120  # "120" is converted to a number
        assert settings["allow_weekend_bookings"] is False
        assert settings["week_start_day"] == "sunday"
        assert settings["org_name"] == "Visal Ltd"
        assert settings["updated_by"] == "ADMIN001"

        entry = AuditLog.query.filter_by(action="settings_updated").one()
        assert "booking_start_time" in entry.description and "org_name" in entry.description

    def test_no_audit_entry_when_nothing_changed(self, client, test_admin):
        self.put(client, {"org_name": get_settings().org_name})
        assert AuditLog.query.filter_by(action="settings_updated").count() == 0

    def test_null_clears_an_optional_limit(self, client, test_admin):
        self.put(client, {"max_advance_days": 30})
        assert get_settings().max_advance_days == 30
        self.put(client, {"max_advance_days": None})
        assert get_settings().max_advance_days is None

    @pytest.mark.parametrize(
        "body, message",
        [
            ({"booking_start_time": "9am"}, "must be 'HH:MM'"),
            ({"booking_start_time": "18:00", "booking_end_time": "06:00"}, "must be earlier than"),
            ({"max_booking_duration_minutes": -5}, "must be a positive integer or null"),
            ({"max_advance_days": "soon"}, "must be a positive integer or null"),
            ({"audit_log_retention_days": -1}, "must be a positive integer or null"),
            ({"daily_summary_hour": 24}, "between 0 and 23"),
            ({"daily_summary_hour": "noon"}, "between 0 and 23"),
            ({"default_schedule_view": "year"}, "default_schedule_view must be one of"),
            ({"week_start_day": "friday"}, "week_start_day must be one of"),
            ({"support_email": "not-an-email"}, "valid email address"),
            ({"org_name": "   "}, "cannot be empty"),
        ],
    )
    def test_invalid_values_are_rejected_and_nothing_is_saved(self, client, test_admin, body, message):
        before = get_settings().org_name
        response = self.put(client, body)
        assert response.status_code == 400
        assert message in response.get_json()["error"]
        assert get_settings().org_name == before

    def test_audit_retention_days_can_be_set(self, client, test_admin):
        self.put(client, {"audit_log_retention_days": 30})
        assert get_settings().audit_log_retention_days == 30


class TestSupportMessages:
    def create(self, client, who="TEST001", **overrides):
        body = {"subject": "Cannot book", "message": "The form keeps failing."}
        body.update(overrides)
        return client.post("/support", json=body, headers=auth(client, who))

    def test_staff_sends_a_message_and_admins_are_notified(self, client, test_user, test_admin):
        response = self.create(client)
        assert response.status_code == 201
        message = response.get_json()["message"]
        assert message["status"] == "open" and message["staff_name"] == "Test User"

        note = Notification.query.filter_by(user_id="ADMIN001", type="support_update").one()
        assert "Cannot book" in note.message
        assert AuditLog.query.filter_by(action="support_message_created").count() == 1

    @pytest.mark.parametrize(
        "overrides, message",
        [
            ({"subject": ""}, "subject and message are required"),
            ({"message": "  "}, "subject and message are required"),
            ({"subject": "x" * 151}, "150 characters or fewer"),
            ({"message": "x" * 2001}, "2000 characters or fewer"),
        ],
    )
    def test_validation(self, client, test_user, overrides, message):
        response = self.create(client, **overrides)
        assert response.status_code == 400
        assert message in response.get_json()["error"]

    def test_staff_only_see_their_own_messages(self, client, test_user, test_admin):
        from app.models.users import User

        db.session.add(User(staff_id="TEST002", full_name="Other", email="o@example.com", password="password123"))
        db.session.commit()
        self.create(client, who="TEST001", subject="Mine")
        self.create(client, who="TEST002", subject="Theirs")

        mine = client.get("/support", headers=auth(client, "TEST001")).get_json()
        assert [m["subject"] for m in mine["messages"]] == ["Mine"]

        everything = client.get("/support", headers=auth(client, "ADMIN001")).get_json()
        assert everything["total"] == 2

    def test_admin_list_is_paged_and_filterable(self, client, test_user, test_admin):
        for index in range(3):
            self.create(client, subject=f"Subject {index}")
        first = SupportMessage.query.first()
        first.status = "resolved"
        db.session.commit()

        headers = auth(client, "ADMIN001")
        page = client.get("/support?page=1&page_size=2", headers=headers).get_json()
        assert page["total"] == 3 and len(page["messages"]) == 2

        open_only = client.get("/support?status=open", headers=headers).get_json()
        assert open_only["total"] == 2

    def test_only_admins_can_reply(self, client, test_user):
        self.create(client)
        message_id = SupportMessage.query.one().id
        response = client.patch(f"/support/{message_id}", json={"admin_reply": "Hi"}, headers=auth(client, "TEST001"))
        assert response.status_code == 403

    def test_admin_reply_notifies_the_requester(self, client, test_user, test_admin):
        self.create(client)
        message_id = SupportMessage.query.one().id

        response = client.patch(
            f"/support/{message_id}", json={"admin_reply": "Try again now"}, headers=auth(client, "ADMIN001")
        )
        assert response.status_code == 200
        body = response.get_json()["message"]
        assert body["admin_reply"] == "Try again now"
        assert body["replied_by_name"] == "Test Admin" and body["replied_at"]

        note = Notification.query.filter_by(user_id="TEST001", type="support_update").one()
        assert "An admin replied" in note.message
        assert AuditLog.query.filter_by(action="support_message_replied").count() == 1

    def test_resolving_notifies_and_is_audited_as_resolved(self, client, test_user, test_admin):
        self.create(client)
        message_id = SupportMessage.query.one().id

        response = client.patch(f"/support/{message_id}", json={"status": "resolved"}, headers=auth(client, "ADMIN001"))
        assert response.status_code == 200
        assert "marked resolved" in Notification.query.filter_by(user_id="TEST001").one().message
        assert AuditLog.query.filter_by(action="support_message_resolved").count() == 1

    @pytest.mark.parametrize(
        "body, message",
        [
            ({}, "Nothing to update"),
            ({"admin_reply": "   "}, "admin_reply cannot be empty"),
            ({"admin_reply": "x" * 2001}, "2000 characters or fewer"),
            ({"status": "archived"}, "status must be one of"),
        ],
    )
    def test_reply_validation(self, client, test_user, test_admin, body, message):
        self.create(client)
        message_id = SupportMessage.query.one().id
        response = client.patch(f"/support/{message_id}", json=body, headers=auth(client, "ADMIN001"))
        assert response.status_code == 400
        assert message in response.get_json()["error"]

    def test_unknown_message_is_404(self, client, test_admin):
        response = client.patch("/support/999", json={"status": "resolved"}, headers=auth(client, "ADMIN001"))
        assert response.status_code == 404
