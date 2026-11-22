"""
Command-line entry points for scheduled work, so a cron job (for example a Render cron service) can run
them instead of the in-process scheduler thread. Set START_EMAIL_SCHEDULER=false when you do.

    flask --app wsgi send-daily-summary [--force]
    flask --app wsgi purge-audit-logs
"""

import click

from .services.maintenance_service import purge_old_audit_logs, run_daily_summary

SUMMARY_MESSAGES = {
    "sent": "Daily summary sent.",
    "failed": "Daily summary could not be sent - see the log.",
    "not_due": "Not due: summaries are off, or today's send hour has not been reached yet.",
    "already_sent": "Already sent today - nothing to do.",
}


def register_cli(app):
    @app.cli.command("send-daily-summary")
    @click.option("--force", is_flag=True, help="Send now, ignoring the schedule and the once-a-day guard.")
    def send_daily_summary(force):
        status = run_daily_summary(force=force)
        click.echo(SUMMARY_MESSAGES[status])
        if status == "failed":
            raise SystemExit(1)

    @app.cli.command("purge-audit-logs")
    def purge_audit_logs():
        removed = purge_old_audit_logs()
        click.echo(f"Removed {removed} audit log entries.")
