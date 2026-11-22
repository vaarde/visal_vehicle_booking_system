from unittest.mock import patch

import pytest

from app import db
from app.extensions import mail
from app.models.audit_log import AuditLog
from app.models.users import User


def auth(client, staff_id, password="password123"):
    response = client.post("/auth/login", json={"staff_id": staff_id, "password": password})
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def add_user(staff_id, name, email, role="staff", status="active", department="Ops"):
    user = User(
        staff_id=staff_id, full_name=name, email=email, password="password123", department=department, role=role
    )
    user.status = status
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def people(app, test_user, test_admin):
    # TEST001 (staff) and ADMIN001 (admin) already exist from the shared fixtures
    add_user("STAFF2", "Kofi Boateng", "kofi@example.com")
    add_user("GONE1", "Left Company", "gone@example.com", status="inactive")
    add_user("ADMIN2", "Second Admin", "admin2@example.com", role="admin")


def send(client, who="ADMIN001", **overrides):
    body = {"subject": "Hello", "html_body": "<p>Hi there</p>", "audience": "all"}
    body.update(overrides)
    return client.post("/broadcast/email", json=body, headers=auth(client, who))


class TestAccess:
    def test_requires_login(self, client):
        assert client.post("/broadcast/email", json={}).status_code == 401
        assert client.get("/broadcast/senders").status_code == 401

    def test_staff_are_refused(self, client, test_user):
        assert send(client, who="TEST001").status_code == 403
        assert client.get("/broadcast/senders", headers=auth(client, "TEST001")).status_code == 403


class TestValidation:
    @pytest.mark.parametrize(
        "overrides, message",
        [
            ({"subject": ""}, "subject and html_body are required"),
            ({"html_body": " "}, "subject and html_body are required"),
            ({"subject": "x" * 201}, "200 characters or fewer"),
            ({"html_body": "x" * 50001}, "50000 characters or fewer"),
            ({"audience": "everyone"}, "audience must be one of"),
            ({"format": "markdown"}, "format must be 'html' or 'text'"),
            ({"audience": "user"}, "target_staff_id is required"),
            ({"sender": "spoof@evil.example"}, "sender is not an allowed address"),
        ],
    )
    def test_bad_requests(self, client, people, overrides, message):
        response = send(client, **overrides)
        assert response.status_code == 400
        assert message in response.get_json()["error"]

    def test_unknown_or_inactive_target_user_is_404(self, client, people):
        assert send(client, audience="user", target_staff_id="NOPE").status_code == 404
        assert send(client, audience="user", target_staff_id="GONE1").status_code == 404


class TestAudiences:
    def recipients(self, client, **overrides):
        with mail.record_messages() as outbox:
            response = send(client, **overrides)
        return response, sorted(address for message in outbox for address in message.recipients)

    def test_all_skips_inactive_users(self, client, people):
        response, addresses = self.recipients(client, audience="all")
        assert response.get_json() == {"success": True, "sent": 4, "failed": 0, "audience": "all"}
        assert "gone@example.com" not in addresses

    def test_staff_only(self, client, people):
        _, addresses = self.recipients(client, audience="staff")
        assert addresses == ["kofi@example.com", "test@example.com"]

    def test_admins_only(self, client, people):
        _, addresses = self.recipients(client, audience="admin")
        assert addresses == ["admin2@example.com", "admin@example.com"]

    def test_single_user(self, client, people):
        _, addresses = self.recipients(client, audience="user", target_staff_id="STAFF2")
        assert addresses == ["kofi@example.com"]

    def test_self_test_send_goes_only_to_the_sender(self, client, people):
        _, addresses = self.recipients(client, audience="self")
        assert addresses == ["admin@example.com"]


class TestContent:
    def test_personalisation_variables(self, client, people):
        with mail.record_messages() as outbox:
            send(
                client,
                audience="user",
                target_staff_id="STAFF2",
                subject="Hi {{first_name}}",
                html_body="<p>{{full_name}} ({{staff_id}}) in {{department}} - {{role}}</p>",
            )
        message = outbox[0]
        assert message.subject == "Hi Kofi"
        assert "Kofi Boateng (STAFF2) in Ops - Staff" in message.html

    def test_html_message_gets_a_plain_text_alternative(self, client, people):
        with mail.record_messages() as outbox:
            send(client, audience="self", html_body="<h1>News</h1><p>Today</p>", format="html")
        assert "<" not in outbox[0].body and "News" in outbox[0].body and outbox[0].html

    def test_text_format_has_no_html_part(self, client, people):
        with mail.record_messages() as outbox:
            send(client, audience="self", html_body="Plain words", format="text")
        assert outbox[0].body == "Plain words" and not outbox[0].html

    def test_allowed_sender_alias(self, client, people, app):
        app.config["BROADCAST_SENDERS"] = "Fleet|fleet@example.com"
        senders = client.get("/broadcast/senders", headers=auth(client, "ADMIN001")).get_json()
        assert senders["senders"] == [{"label": "Fleet", "email": "fleet@example.com"}]
        assert "first_name" in senders["variables"]

        with mail.record_messages() as outbox:
            response = send(client, audience="self", sender="fleet@example.com")
        assert response.status_code == 200
        assert outbox[0].sender == "fleet@example.com"


class TestDeliveryResults:
    def test_partial_failure_is_counted_and_audited(self, client, people):
        calls = {"count": 0}

        def flaky(message):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("mailbox full")

        with patch("app.routes.broadcast.mail.send", side_effect=flaky):
            body = send(client, audience="all").get_json()

        assert (body["sent"], body["failed"]) == (3, 1)
        entry = AuditLog.query.filter_by(action="broadcast_email_sent").one()
        assert "to 3 recipient(s)" in entry.description and "1 failed" in entry.description

    def test_total_failure_is_a_502_and_not_audited(self, client, people):
        with patch("app.routes.broadcast.mail.send", side_effect=RuntimeError("smtp down")):
            response = send(client, audience="all")
        assert response.status_code == 502
        assert AuditLog.query.filter_by(action="broadcast_email_sent").count() == 0

    def test_no_recipients(self, client, test_admin):
        # the only other account is an admin with no email, and the original admin is deactivated,
        # so the "all" audience resolves to nobody
        db.session.get(User, "ADMIN001").status = "inactive"
        add_user("ADMIN3", "Third", "third@example.com", role="admin")
        db.session.get(User, "ADMIN3").email = ""
        db.session.commit()

        response = send(client, who="ADMIN3", audience="all")
        assert response.status_code == 400
        assert response.get_json()["error"] == "No recipients found for the selected audience"

    def test_success_is_audited(self, client, people):
        send(client, audience="staff", subject="Policy update")
        entry = AuditLog.query.filter_by(action="broadcast_email_sent").one()
        assert 'Sent broadcast email "Policy update" to 2 recipient(s) (requesters only)' == entry.description
