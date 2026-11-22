# Backend Documentation

## Overview
Flask + SQLAlchemy API for the VISAL vehicle booking system. Staff request vehicle bookings, admins approve or decline them, and the system sends in-app notifications and emails. Authentication is JWT (Flask-JWT-Extended); the database is PostgreSQL (SQLite in tests); schema changes are managed with Alembic.

## Application Structure
- `app/__init__.py` - application factory (`create_app`): config, extensions, blueprints, JWT revocation check, JSON error handler, CLI commands, scheduler start.
- `app/extensions.py` - shared extensions (`db`, `migrate`, `mail`).
- `app/exceptions.py` - typed errors (`BadRequestError`, `ForbiddenError`, `NotFoundError`, ...). Services and routes raise these; one handler returns `{"error": "<message>"}` with the right status.
- `app/models/` - SQLAlchemy models: users, bookings, notifications, changelog, audit_log, app_settings, support_message, token_blocklist.
- `app/routes/` - HTTP layer only: parse the request, check the role, call a service, return JSON.
- `app/services/` - business rules (see below).
- `app/cli.py` - `flask` commands for scheduled work.
- `config.py` - `Config` (environment variables) and `TestConfig`.
- `alembic/` - migrations (always write a working `downgrade()`).
- `scripts/` - manual helpers, run from `backend/` as `python -m scripts.<name>` (`db_test`, `email_manual`, `send_approval_email_test`, `send_decline_email_test`).
- `tests/` - pytest suite.

## API Endpoints Map
"Admin" means a token whose role is `admin`. Errors are always `{"error": "message"}`.

### Auth and staff (`/auth`)
- `POST /auth/login` - staff ID + password; returns the token. Locks a staff ID + IP after repeated failures (429).
- `POST /auth/logout` - revokes the current token.
- `GET /auth/me`, `PUT /auth/me` - own profile (name and phone only).
- `PATCH /auth/me/password` - change own password (min 8 characters).
- `POST /auth/heartbeat`, `GET /auth/online-users` - presence.
- Admin only: `POST /auth/register`, `GET /auth/users`, `PUT /auth/users/<staff_id>`, `PATCH /auth/users/<staff_id>/status` (`active` / `inactive`).
- Admin only, bulk import: `POST /auth/users/import` (multipart `file`, optional `dry_run=true`) and `GET /auth/users/import/template`.

### Bookings (`/bookings`) - all need a token
- `POST /bookings/create_booking` - validated against the admin settings (booking window, weekends, max duration, lead time, max advance).
- `GET /bookings/staff/<staff_id>` - a staff member's bookings (own only; admins can read anyone's).
- `GET /bookings/schedule_view` - shared schedule. Notes and admin comments on other people's bookings are hidden from non-admins.
- `GET /bookings/pending` - admin only.
- `PATCH /bookings/<id>/approve` - admin; refused once the booking's time has passed.
- `PATCH /bookings/<id>/decline` - admin; a reason is required when the setting says so.
- `PATCH /bookings/<id>/cancel` - owner or admin; not for past bookings.
- The three list endpoints accept `?from=YYYY-MM-DD&to=YYYY-MM-DD&status=...` (not `/pending`) and optional paging `?page=&page_size=` (max 200). Without paging they return everything, as the calendar needs.

### Reports (`/reports`) - admin only
`GET /reports/summary` (figures and chart data), `GET /reports/bookings` (paged table), `GET /reports/export.csv`. All accept `from`, `to`, `status`, `department`. CSV cells that start with `= + - @` are prefixed with `'` so they cannot run as spreadsheet formulas.

### Other
- `GET/PUT /settings` - read (any user) / update (admin) the booking rules, schedule defaults, notification options, audit retention and branding.
- `GET /notifications`, `PATCH /notifications/<id>/read`, `PATCH /notifications/mark_all_read`.
- `POST /support`, `GET /support`, `PATCH /support/<id>` - support messages (staff send, admins reply/resolve).
- `GET /broadcast/senders`, `POST /broadcast/email` - admin broadcast email.
- `GET /audit-log` - admin audit trail.
- `GET /changelog/`, `GET /changelog/<id>` (published only, public); `?include_unpublished=true` needs an admin token. Admin: `POST /changelog/create`, `PUT /changelog/<id>`, `PATCH /changelog/<id>/publish`, `DELETE /changelog/<id>`.
- `GET /auth/health` - health check.

## Security model
- **Tokens are checked on every request.** A token is refused (401, "Session is no longer valid") if it was logged out (`token_blocklist`), if the account was deactivated or deleted, or if the user's role changed since it was issued.
- **Login throttling** (`services/login_throttle.py`): `LOGIN_MAX_ATTEMPTS` failures per staff ID + IP within `LOGIN_WINDOW_SECONDS`, then 429. It is held in memory, so each worker process counts separately.
- **Roles** are `staff` and `admin`; anything else is rejected on register, update and import.
- **No secrets in code.** `DATABASE_URL` is required; a missing `SECRET_KEY` / `JWT_SECRET_KEY` falls back to a random per-process value (and logs a warning), so set both in production.
- Known gap: the frontend keeps the token in `localStorage` (issue #15).

## Services
- `services/booking_service.py` - validation, create / approve / decline / cancel (one transaction each, including the audit entry and notifications), listing filters and paging.
- `services/email_service.py` - booking approve/decline emails are sent on a background thread (synchronously under `TESTING`); `send_daily_booking_summary()` emails admins the upcoming bookings.
- `services/maintenance_service.py` - `run_daily_summary()` and `purge_old_audit_logs()`, shared by the scheduler and the CLI.
- `services/summary_scheduler.py` - a daemon thread that wakes every minute. It sends the summary when it is enabled, past `daily_summary_hour` (set in Settings) and not yet sent today. Only one worker can send: the day is claimed with an atomic update of `app_settings.last_summary_sent_on`. It also purges old audit entries once a day. It waits a minute before its first check, so migrations and CLI commands never trigger a send.
- `services/report_service.py`, `services/staff_import_service.py`, `services/settings_service.py`, `services/audit_service.py`, `services/login_throttle.py`.
- `audit_service.log_action(..., commit=False)` writes the audit entry in the caller's transaction.

## Scheduled work and cron
The in-process scheduler is the default. To use Render cron instead, set `START_EMAIL_SCHEDULER=false` and run from `backend/`:

```
flask --app wsgi send-daily-summary [--force]   # sends only if due and not yet sent today
flask --app wsgi purge-audit-logs                # deletes entries older than audit_log_retention_days
```

## Configuration (environment variables)
- `DATABASE_URL` (required), `SECRET_KEY`, `JWT_SECRET_KEY`, `JWT_ACCESS_TOKEN_EXPIRES_HOURS` (default 8).
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USE_SSL`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER`.
- `BROADCAST_SENDERS` - optional extra from-addresses, `Label|email,Label2|email2`.
- `START_EMAIL_SCHEDULER` (default true), `CORS_ORIGINS` (comma-separated).
- `LOGIN_MAX_ATTEMPTS` (default 5), `LOGIN_WINDOW_SECONDS` (default 900).
- The summary hour is a setting in the app (Settings screen), not an environment variable; `BOOKING_SUMMARY_HOUR` in `config.py` is not used by anything.
- `TestConfig` uses in-memory SQLite and suppresses email.

## Testing
From `backend/`: `python -m pytest` (about 20 seconds, 240 tests).
- Tests use `db.create_all()`, not migrations, so a passing suite does not prove a migration works. After a schema change run `alembic upgrade head`, `alembic downgrade -1`, `alembic upgrade head` against a **local** database (set `PYTHONPATH=.`).
- Test files: `test_security`, `test_bookings`, `test_booking_notifications`, `test_notification_routes`, `test_email_service`, `test_maintenance`, `test_reports`, `test_staff_import`, `test_staff_admin`, `test_settings_and_support`, `test_broadcast`, `test_changelog`.
- `conftest.py` provides the app, client, `test_user` (`TEST001`), `test_admin` (`ADMIN001`) and `test_booking` fixtures. Tests use a cheap password hash for speed.

## Database
- Production: PostgreSQL via `DATABASE_URL`. Development: local PostgreSQL. Tests: in-memory SQLite.
- Migrations: `alembic/versions/`. Latest: `b8c9d0e1f2a3` (adds `app_settings.last_summary_sent_on`), after `a7b8c9d0e1f2` (adds `token_blocklist`).
- Never run migrations or scripts against the hosted database from a development machine.

## Keeping this document accurate
Update the endpoint map, services and configuration sections in the same change as the code.

## Change Log / Notes
- `2026-04-19`: Initial backend documentation created.
- `2026-11-22`: Rewritten to match the code. Added the security model, booking service, reports, bulk staff import, cron commands and the current test suite.

---
