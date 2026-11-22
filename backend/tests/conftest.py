import pytest
from werkzeug.security import generate_password_hash
from app import create_app, db
from app.models.users import User
from app.models.bookings import Booking
from app.models.notifications import Notification
from app.services import login_throttle
from config import TestConfig


@pytest.fixture(autouse=True)
def fast_password_hashing(monkeypatch):
    """
    Werkzeug's default hash (scrypt) is deliberately slow, and nearly every test logs in. Use a cheap
    hash in tests only; check_password_hash reads the method from the stored hash, so verification
    works either way. Production hashing is unchanged.
    """
    monkeypatch.setattr(
        "app.models.users.generate_password_hash",
        lambda password: generate_password_hash(password, method="pbkdf2:sha256:1000"),
    )


@pytest.fixture
def app():
    """Create and configure a test app instance."""
    app = create_app(TestConfig)
    login_throttle.clear_all()

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """A test client for the app."""
    return app.test_client()


@pytest.fixture
def test_user(app):
    """Create a test user."""
    user = User(
        staff_id='TEST001',
        full_name='Test User',
        email='test@example.com',
        password='password123',
        department='IT',
        role='staff'
    )
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def test_admin(app):
    """Create a test admin user."""
    admin = User(
        staff_id='ADMIN001',
        full_name='Test Admin',
        email='admin@example.com',
        password='password123',
        department='Admin',
        role='admin'
    )
    db.session.add(admin)
    db.session.commit()
    return admin


@pytest.fixture
def test_booking(app, test_user):
    """Create a test booking."""
    from datetime import date, time, timedelta
    # tomorrow, so approving it never depends on what time of day the tests run
    booking = Booking(
        user_id=test_user.staff_id,
        booking_date=date.today() + timedelta(days=1),
        start_time=time(10, 0),
        end_time=time(12, 0),
        location='Test Location',
        purpose='Test Purpose',
        status='Pending'
    )
    db.session.add(booking)
    db.session.commit()
    return booking