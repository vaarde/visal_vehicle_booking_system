# app/__init__.py
from flask import Flask, jsonify, make_response
from .extensions import db, migrate, mail
from flask_cors import CORS
from flask_jwt_extended import JWTManager

def create_app(config_class=None):
    app = Flask(__name__)

    # Load config
    if config_class is None:
        from config import Config
        app.config.from_object(Config)
    else:
        app.config.from_object(config_class)

    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        raise RuntimeError("DATABASE_URL is not set. Add it to the environment or backend/.env.")

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    mail.init_app(app)

    # Import models from models folder
    from .models.users import User
    from .models.bookings import Booking
    from .models.notifications import Notification
    from .models.changelog import Changelog
    from .models.audit_log import AuditLog
    from .models.app_settings import AppSettings
    from .models.support_message import SupportMessage
    from .models.token_blocklist import TokenBlocklist

    # Register blueprints
    from .routes.auth import auth_bp
    from .routes.bookings import bookings_bp
    from .routes.notifications import notifications_bp
    from .routes.changelog import changelog_bp
    from .routes.audit import audit_bp
    from .routes.settings import settings_bp
    from .routes.support import support_bp
    from .routes.broadcast import broadcast_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(bookings_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(changelog_bp)
    app.register_blueprint(audit_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(support_bp)
    app.register_blueprint(broadcast_bp)

    # JWT token initialization
    jwt = JWTManager(app)

    @jwt.token_in_blocklist_loader
    def is_token_revoked(jwt_header, jwt_payload):
        """
        Reject a token when it was logged out, its account was deactivated or
        deleted, or the user's role changed since it was issued (forces a re-login
        so stale privileges can't outlive an admin's change).
        """
        if TokenBlocklist.query.filter_by(jti=jwt_payload["jti"]).first():
            return True
        user = db.session.get(User, jwt_payload["sub"])
        if user is None or user.status != "active":
            return True
        claimed_role = jwt_payload.get("role")
        return claimed_role is not None and claimed_role != user.role

    from .exceptions import AppError

    @app.errorhandler(AppError)
    def handle_app_error(error):
        return jsonify({"error": error.message}), error.status_code

    @jwt.revoked_token_loader
    def revoked_token_response(jwt_header, jwt_payload):
        return jsonify({"error": "Session is no longer valid. Please sign in again."}), 401

    # Enable CORS with configuration from config
    CORS(app,
         origins=app.config['CORS_ORIGINS'],
         methods=app.config['CORS_METHODS'],
         allow_headers=app.config['CORS_HEADERS'],
         supports_credentials=app.config['CORS_SUPPORTS_CREDENTIALS'])

    if app.config.get('START_EMAIL_SCHEDULER', False) and not app.config.get('TESTING', False):
        from .services.summary_scheduler import start_daily_summary_scheduler
        start_daily_summary_scheduler(app)

    return app
