from .. import db
from ..models.audit_log import AuditLog


def log_action(
    actor_id=None, actor_name=None, action=None, target_type=None, target_id=None, description=None, commit=True
):
    """
    Record an audit trail entry. Never raises - a logging failure should not break the calling request.

    Pass commit=False to write the entry in the caller's transaction, so it is saved (or rolled back)
    together with the change it describes.
    """
    try:
        entry = AuditLog(
            actor_id=actor_id,
            actor_name=actor_name,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            description=description,
        )
        db.session.add(entry)
        if commit:
            db.session.commit()
    except Exception:
        if commit:
            db.session.rollback()
