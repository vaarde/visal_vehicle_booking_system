from datetime import date, datetime, time, timedelta

import pytest

from app import db
from app.models.audit_log import AuditLog
from app.models.bookings import Booking
from app.models.notifications import Notification
from app.models.users import User
from app.services.settings_service import get_settings

MONDAY = "2030-01-07"
SATURDAY = "2030-01-12"


def auth(client, staff_id, password="password123"):
    response = client.post("/auth/login", json={"staff_id": staff_id, "password": password})
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def payload(**overrides):
    data = {
        "booking_date": MONDAY,
        "start_time": "09:00",
        "end_time": "10:00",
        "location": "Tema",
        "purpose": "Client visit",
    }
    data.update(overrides)
    return data


def make_booking(user_id, booking_date, start=time(9, 0), end=time(10, 0), status="Pending"):
    booking = Booking(
        user_id=user_id,
        booking_date=booking_date,
        start_time=start,
        end_time=end,
        location="Accra",
        purpose="Test",
        status=status,
    )
    db.session.add(booking)
    db.session.commit()
    return booking


class TestCreateBookingRules:
    def create(self, client, **overrides):
        return client.post("/bookings/create_booking", headers=auth(client, "TEST001"), json=payload(**overrides))

    def test_creates_booking_audit_entry_and_admin_notification(self, client, test_user, test_admin):
        response = self.create(client)
        assert response.status_code == 201
        booking = response.get_json()["booking"]
        assert booking["status"] == "Pending"
        assert booking["staff_name"] == "Test User"

        assert AuditLog.query.filter_by(action="booking_created", target_id=str(booking["booking_id"])).count() == 1
        note = Notification.query.filter_by(user_id="ADMIN001", type="submitted").one()
        assert note.booking_id == booking["booking_id"]

    @pytest.mark.parametrize("missing", ["booking_date", "start_time", "end_time", "location", "purpose"])
    def test_required_fields(self, client, test_user, missing):
        response = self.create(client, **{missing: ""})
        assert response.status_code == 400
        assert "are required" in response.get_json()["error"]

    def test_rejects_bad_date_format(self, client, test_user):
        response = self.create(client, booking_date="07/01/2030")
        assert response.status_code == 400
        assert response.get_json()["error"] == "Invalid date or time format"

    def test_start_must_be_before_end(self, client, test_user):
        response = self.create(client, start_time="10:00", end_time="09:00")
        assert response.status_code == 400

    def test_outside_booking_window(self, client, test_user):
        response = self.create(client, start_time="04:00", end_time="05:00")
        assert response.status_code == 400
        assert "must fall between 06:00 and 18:00" in response.get_json()["error"]

    def test_weekend_rule(self, client, test_user):
        settings = get_settings()
        settings.allow_weekend_bookings = False
        db.session.commit()

        assert self.create(client, booking_date=SATURDAY).get_json()["error"] == "Weekend bookings are not allowed"
        assert self.create(client, booking_date=MONDAY).status_code == 201

    def test_max_duration_rule(self, client, test_user):
        settings = get_settings()
        settings.max_booking_duration_minutes = 60
        db.session.commit()

        response = self.create(client, start_time="09:00", end_time="11:00")
        assert response.status_code == 400
        assert "cannot exceed 60 minutes" in response.get_json()["error"]

    def test_max_advance_rule(self, client, test_user):
        settings = get_settings()
        settings.max_advance_days = 30
        db.session.commit()

        response = self.create(client)  # 2030 is far beyond 30 days
        assert response.status_code == 400
        assert "more than 30 days in advance" in response.get_json()["error"]

    def test_min_lead_time_rule(self, client, test_user):
        settings = get_settings()
        settings.min_lead_time_minutes = 60 * 24 * 365 * 10  # ten years of notice
        settings.max_advance_days = None
        db.session.commit()

        response = self.create(client, booking_date="2027-01-04")
        assert response.status_code == 400
        assert "advance notice" in response.get_json()["error"]


class TestApprovalRules:
    def test_cannot_approve_a_booking_whose_time_has_passed(self, client, test_admin, test_user):
        yesterday = date.today() - timedelta(days=1)
        booking = make_booking("TEST001", yesterday)

        response = client.patch(f"/bookings/{booking.id}/approve", headers=auth(client, "ADMIN001"), json={})
        assert response.status_code == 400
        assert "already passed" in response.get_json()["error"]
        assert db.session.get(Booking, booking.id).status == "Pending"

    def test_past_booking_can_still_be_declined(self, client, test_admin, test_user):
        booking = make_booking("TEST001", date.today() - timedelta(days=1))

        response = client.patch(
            f"/bookings/{booking.id}/decline", headers=auth(client, "ADMIN001"), json={"admin_comment": "Too late"}
        )
        assert response.status_code == 200

    def test_approval_writes_audit_entry_with_the_change(self, client, test_admin, test_user):
        booking = make_booking("TEST001", date.today() + timedelta(days=2))

        response = client.patch(
            f"/bookings/{booking.id}/approve", headers=auth(client, "ADMIN001"), json={"admin_comment": "OK"}
        )
        assert response.status_code == 200
        entry = AuditLog.query.filter_by(action="booking_approved", target_id=str(booking.id)).one()
        assert entry.actor_id == "ADMIN001"

    def test_staff_cannot_approve(self, client, test_user):
        booking = make_booking("TEST001", date.today() + timedelta(days=2))
        response = client.patch(f"/bookings/{booking.id}/approve", headers=auth(client, "TEST001"), json={})
        assert response.status_code == 403

    def test_unknown_booking_is_404(self, client, test_admin):
        response = client.patch("/bookings/9999/approve", headers=auth(client, "ADMIN001"), json={})
        assert response.status_code == 404
        assert response.get_json()["error"] == "Booking not found"


class TestCancelRules:
    def test_staff_cannot_cancel_someone_elses_booking(self, client, test_user):
        other = User(staff_id="TEST002", full_name="Other", email="o@example.com", password="password123")
        db.session.add(other)
        db.session.commit()
        booking = make_booking("TEST001", date.today() + timedelta(days=2))

        response = client.patch(f"/bookings/{booking.id}/cancel", headers=auth(client, "TEST002"))
        assert response.status_code == 403

    def test_admin_cancel_notifies_the_requester_and_names_the_owner(self, client, test_admin, test_user):
        booking = make_booking("TEST001", date.today() + timedelta(days=2))

        response = client.patch(f"/bookings/{booking.id}/cancel", headers=auth(client, "ADMIN001"))
        assert response.status_code == 200

        to_requester = Notification.query.filter_by(user_id="TEST001", type="cancelled").one()
        assert "cancelled by an administrator" in to_requester.message
        to_admin = Notification.query.filter_by(user_id="ADMIN001", type="cancelled").one()
        assert "Test User" in to_admin.message  # the booking owner, not the admin who clicked cancel

    def test_cannot_cancel_a_past_booking(self, client, test_user):
        booking = make_booking("TEST001", date.today() - timedelta(days=1))
        response = client.patch(f"/bookings/{booking.id}/cancel", headers=auth(client, "TEST001"))
        assert response.status_code == 400
        assert response.get_json()["error"] == "Past bookings cannot be cancelled"


class TestListingFiltersAndPaging:
    @pytest.fixture
    def many_bookings(self, test_user, test_admin):
        today = date.today()
        for offset in range(5):
            make_booking("TEST001", today + timedelta(days=offset), status="Pending" if offset % 2 == 0 else "Approved")
        return today

    def test_unpaged_response_is_unchanged(self, client, many_bookings):
        data = client.get("/bookings/schedule_view", headers=auth(client, "ADMIN001")).get_json()
        assert data["total_bookings"] == 5
        assert len(data["bookings"]) == 5
        assert "page" not in data

    def test_paging(self, client, many_bookings):
        headers = auth(client, "ADMIN001")
        first = client.get("/bookings/schedule_view?page=1&page_size=2", headers=headers).get_json()
        assert len(first["bookings"]) == 2
        assert first["total_bookings"] == 5
        assert first["total_pages"] == 3

        last = client.get("/bookings/schedule_view?page=3&page_size=2", headers=headers).get_json()
        assert len(last["bookings"]) == 1

    def test_date_range_and_status_filters(self, client, many_bookings):
        today = many_bookings
        headers = auth(client, "ADMIN001")

        in_range = client.get(
            f"/bookings/schedule_view?from={today + timedelta(days=1)}&to={today + timedelta(days=2)}", headers=headers
        ).get_json()
        assert in_range["total_bookings"] == 2

        approved = client.get("/bookings/schedule_view?status=Approved", headers=headers).get_json()
        assert approved["total_bookings"] == 2

    def test_invalid_date_filter_is_a_400(self, client, many_bookings):
        response = client.get("/bookings/schedule_view?from=yesterday", headers=auth(client, "ADMIN001"))
        assert response.status_code == 400
        assert "from must be a date" in response.get_json()["error"]

    def test_pending_queue_is_paged_and_only_pending(self, client, many_bookings):
        data = client.get("/bookings/pending?page=1&page_size=2", headers=auth(client, "ADMIN001")).get_json()
        assert data["total_pending"] == 3
        assert len(data["pending_bookings"]) == 2
        assert all(b["status"] == "Pending" for b in data["pending_bookings"])

    def test_staff_history_is_newest_first(self, client, many_bookings):
        data = client.get("/bookings/staff/TEST001", headers=auth(client, "TEST001")).get_json()
        dates = [b["booking_date"] for b in data["bookings"]]
        assert dates == sorted(dates, reverse=True)
        assert data["staff"]["staff_id"] == "TEST001"
        assert datetime.fromisoformat(data["bookings"][0]["created_at"])
