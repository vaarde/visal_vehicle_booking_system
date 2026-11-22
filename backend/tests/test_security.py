from datetime import date, time

import pytest

from app import create_app, db
from app.models.bookings import Booking
from app.models.users import User


def login(client, staff_id, password="password123"):
    return client.post("/auth/login", json={"staff_id": staff_id, "password": password})


def auth(client, staff_id, password="password123"):
    response = login(client, staff_id, password)
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


@pytest.fixture
def other_user(app):
    user = User(staff_id="TEST002", full_name="Other User", email="other@example.com", password="password123")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def private_booking(app, test_user):
    booking = Booking(
        user_id=test_user.staff_id,
        booking_date=date(2030, 1, 7),
        start_time=time(9, 0),
        end_time=time(10, 0),
        location="Tema",
        purpose="Client visit",
        notes="Bring the signed contract",
    )
    db.session.add(booking)
    db.session.commit()
    return booking


class TestBookingReadAccess:
    @pytest.mark.parametrize("url", ["/bookings/schedule_view", "/bookings/pending", "/bookings/staff/TEST001"])
    def test_requires_login(self, client, url):
        assert client.get(url).status_code == 401

    def test_staff_can_read_own_bookings(self, client, test_user, private_booking):
        response = client.get("/bookings/staff/TEST001", headers=auth(client, "TEST001"))
        assert response.status_code == 200
        assert response.get_json()["total_bookings"] == 1

    def test_staff_cannot_read_someone_elses_bookings(self, client, test_user, other_user, private_booking):
        response = client.get("/bookings/staff/TEST001", headers=auth(client, "TEST002"))
        assert response.status_code == 403

    def test_admin_can_read_any_staff_bookings(self, client, test_admin, test_user, private_booking):
        response = client.get("/bookings/staff/TEST001", headers=auth(client, "ADMIN001"))
        assert response.status_code == 200

    def test_pending_is_admin_only(self, client, test_admin, test_user, private_booking):
        assert client.get("/bookings/pending", headers=auth(client, "TEST001")).status_code == 403
        assert client.get("/bookings/pending", headers=auth(client, "ADMIN001")).status_code == 200

    def test_schedule_hides_notes_of_other_staff(self, client, test_admin, test_user, other_user, private_booking):
        as_other = client.get("/bookings/schedule_view", headers=auth(client, "TEST002")).get_json()["bookings"][0]
        assert as_other["notes"] is None
        assert as_other["purpose"] == "Client visit"  # shared schedule still shows what/where/when

        as_owner = client.get("/bookings/schedule_view", headers=auth(client, "TEST001")).get_json()["bookings"][0]
        assert as_owner["notes"] == "Bring the signed contract"

        as_admin = client.get("/bookings/schedule_view", headers=auth(client, "ADMIN001")).get_json()["bookings"][0]
        assert as_admin["notes"] == "Bring the signed contract"


class TestSessionRevocation:
    def test_deactivated_user_token_stops_working(self, client, test_admin, test_user):
        staff_headers = auth(client, "TEST001")
        assert client.get("/auth/me", headers=staff_headers).status_code == 200

        deactivate = client.patch(
            "/auth/users/TEST001/status", headers=auth(client, "ADMIN001"), json={"status": "inactive"}
        )
        assert deactivate.status_code == 200

        response = client.get("/auth/me", headers=staff_headers)
        assert response.status_code == 401
        assert "sign in again" in response.get_json()["error"]

    def test_logout_revokes_the_token(self, client, test_user):
        headers = auth(client, "TEST001")
        assert client.post("/auth/logout", headers=headers).status_code == 200
        assert client.get("/auth/me", headers=headers).status_code == 401

    def test_role_change_invalidates_old_token(self, client, test_admin, test_user):
        staff_headers = auth(client, "TEST001")
        promote = client.put("/auth/users/TEST001", headers=auth(client, "ADMIN001"), json={"role": "admin"})
        assert promote.status_code == 200
        assert client.get("/auth/me", headers=staff_headers).status_code == 401
        # a fresh login carries the new role
        assert client.get("/bookings/pending", headers=auth(client, "TEST001")).status_code == 200


class TestRoleValidation:
    def test_register_rejects_unknown_role(self, client, test_admin):
        response = client.post(
            "/auth/register",
            headers=auth(client, "ADMIN001"),
            json={
                "staff_id": "NEW1",
                "full_name": "New",
                "email": "new@example.com",
                "password": "password123",
                "role": "superuser",
            },
        )
        assert response.status_code == 400
        assert "role must be one of" in response.get_json()["error"]

    def test_update_rejects_unknown_role(self, client, test_admin, test_user):
        response = client.put("/auth/users/TEST001", headers=auth(client, "ADMIN001"), json={"role": "root"})
        assert response.status_code == 400
        assert User.query.get("TEST001").role == "staff"


class TestLoginThrottle:
    def test_locks_after_too_many_failures(self, client, test_user):
        for _ in range(3):
            assert login(client, "TEST001", "wrong-password").status_code == 401

        # even the right password is refused while locked
        response = login(client, "TEST001")
        assert response.status_code == 429
        assert "Too many failed sign-in attempts" in response.get_json()["error"]

    def test_successful_login_resets_the_counter(self, client, test_user):
        for _ in range(2):
            login(client, "TEST001", "wrong-password")
        assert login(client, "TEST001").status_code == 200
        for _ in range(2):
            assert login(client, "TEST001", "wrong-password").status_code == 401

    def test_lock_is_per_account(self, client, test_user, test_admin):
        for _ in range(3):
            login(client, "TEST001", "wrong-password")
        assert login(client, "ADMIN001").status_code == 200


class TestConfig:
    def test_database_url_is_required(self):
        class NoDatabase:
            SQLALCHEMY_DATABASE_URI = None

        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            create_app(NoDatabase)
