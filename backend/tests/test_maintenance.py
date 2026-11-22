from datetime import datetime, timedelta
from unittest.mock import patch

from flask_mail import Message

from app import db
from app.models.audit_log import AuditLog
from app.services import email_service, maintenance_service, summary_scheduler
from app.services.settings_service import get_settings

AFTER_HOUR = datetime(2030, 1, 7, 18, 0)  # default summary hour is 17:00
BEFORE_HOUR = datetime(2030, 1, 7, 9, 0)

SEND = "app.services.maintenance_service.send_daily_booking_summary"


class TestSummaryIsDue:
    def test_not_due_before_the_send_hour(self, app):
        assert maintenance_service.summary_is_due(BEFORE_HOUR) is False

    def test_due_after_the_send_hour(self, app):
        assert maintenance_service.summary_is_due(AFTER_HOUR) is True

    def test_not_due_when_disabled(self, app):
        get_settings().daily_summary_enabled = False
        db.session.commit()
        assert maintenance_service.summary_is_due(AFTER_HOUR) is False

    def test_changed_hour_applies_immediately(self, app):
        settings = get_settings()
        settings.daily_summary_hour = 20
        db.session.commit()
        assert maintenance_service.summary_is_due(AFTER_HOUR) is False  # 18:00 is now too early


class TestRunDailySummary:
    def test_sends_once_per_day(self, app):
        with patch(SEND, return_value=True) as send:
            assert maintenance_service.run_daily_summary(AFTER_HOUR) == "sent"
            assert maintenance_service.run_daily_summary(AFTER_HOUR) == "not_due"  # already sent: no longer due
            assert send.call_count == 1

    def test_next_day_sends_again(self, app):
        with patch(SEND, return_value=True) as send:
            maintenance_service.run_daily_summary(AFTER_HOUR)
            assert maintenance_service.run_daily_summary(AFTER_HOUR + timedelta(days=1)) == "sent"
            assert send.call_count == 2

    def test_only_one_worker_can_claim_the_day(self, app):
        today = AFTER_HOUR.date()
        assert maintenance_service._claim_summary_day(today) is True
        assert maintenance_service._claim_summary_day(today) is False  # a second worker loses the race

    def test_losing_the_claim_means_no_email(self, app):
        with patch(SEND, return_value=True) as send, patch.object(
            maintenance_service, "_claim_summary_day", return_value=False
        ):
            assert maintenance_service.run_daily_summary(AFTER_HOUR) == "already_sent"
            send.assert_not_called()

    def test_not_due_sends_nothing(self, app):
        with patch(SEND, return_value=True) as send:
            assert maintenance_service.run_daily_summary(BEFORE_HOUR) == "not_due"
            send.assert_not_called()

    def test_reports_failure(self, app):
        with patch(SEND, return_value=False):
            assert maintenance_service.run_daily_summary(AFTER_HOUR) == "failed"

    def test_force_ignores_schedule_and_guard(self, app):
        with patch(SEND, return_value=True) as send:
            maintenance_service.run_daily_summary(AFTER_HOUR)
            assert maintenance_service.run_daily_summary(BEFORE_HOUR, force=True) == "sent"
            assert send.call_count == 2


class TestAuditRetention:
    def add_entry(self, days_old):
        entry = AuditLog(action="login", description="x", created_at=datetime.utcnow() - timedelta(days=days_old))
        db.session.add(entry)
        db.session.commit()

    def test_keeps_everything_when_retention_is_not_set(self, app):
        self.add_entry(1000)
        assert maintenance_service.purge_old_audit_logs() == 0
        assert AuditLog.query.count() == 1

    def test_removes_only_entries_older_than_retention(self, app):
        get_settings().audit_log_retention_days = 90
        db.session.commit()
        self.add_entry(200)
        self.add_entry(100)
        self.add_entry(10)

        assert maintenance_service.purge_old_audit_logs() == 2

        actions = [e.action for e in AuditLog.query.order_by(AuditLog.id)]
        assert actions == ["login", "audit_log_purged"]  # the recent entry plus a record of the purge
        assert (
            "Removed 2 audit log entries older than 90 days"
            in AuditLog.query.filter_by(action="audit_log_purged").one().description
        )

    def test_no_purge_entry_when_nothing_was_removed(self, app):
        get_settings().audit_log_retention_days = 90
        db.session.commit()
        self.add_entry(1)
        assert maintenance_service.purge_old_audit_logs() == 0
        assert AuditLog.query.filter_by(action="audit_log_purged").count() == 0


class TestSchedulerTick:
    def test_purges_once_per_day(self, app):
        with patch(SEND, return_value=True), patch.object(summary_scheduler, "purge_old_audit_logs") as purge:
            last = summary_scheduler.run_tick(now=AFTER_HOUR)
            assert last == AFTER_HOUR.date()
            last = summary_scheduler.run_tick(now=AFTER_HOUR + timedelta(minutes=1), last_purge_date=last)
            assert purge.call_count == 1

            summary_scheduler.run_tick(now=AFTER_HOUR + timedelta(days=1), last_purge_date=last)
            assert purge.call_count == 2


class TestCli:
    def test_send_daily_summary_command(self, app):
        runner = app.test_cli_runner()
        with patch(SEND, return_value=True):
            # run_daily_summary uses the real clock, so force it to avoid depending on the time of day
            result = runner.invoke(args=["send-daily-summary", "--force"])
        assert result.exit_code == 0
        assert "Daily summary sent." in result.output

    def test_send_daily_summary_failure_exits_non_zero(self, app):
        runner = app.test_cli_runner()
        with patch(SEND, return_value=False):
            result = runner.invoke(args=["send-daily-summary", "--force"])
        assert result.exit_code == 1

    def test_purge_command(self, app):
        result = app.test_cli_runner().invoke(args=["purge-audit-logs"])
        assert result.exit_code == 0
        assert "Removed 0 audit log entries." in result.output


class TestBackgroundEmail:
    def message(self):
        return Message(subject="Hi", recipients=["a@example.com"], body="x")

    def test_outside_tests_mail_is_sent_on_a_thread(self, app):
        app.config["TESTING"] = False
        with patch.object(email_service, "mail") as mail, patch.object(email_service.threading, "Thread") as thread:
            email_service._deliver(self.message())

            mail.send.assert_not_called()  # the request did not wait for SMTP
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()

            thread.call_args.kwargs["target"]()  # run what the thread would run
            mail.send.assert_called_once()

    def test_background_failure_is_logged_not_raised(self, app, caplog):
        app.config["TESTING"] = False
        with patch.object(email_service, "mail") as mail, patch.object(email_service.threading, "Thread") as thread:
            mail.send.side_effect = Exception("SMTP down")
            email_service._deliver(self.message())
            thread.call_args.kwargs["target"]()

        assert "Failed to send email to a@example.com: SMTP down" in caplog.text
