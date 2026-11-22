from datetime import datetime

from .. import db


class TokenBlocklist(db.Model):
    """JWTs that were revoked before they expired (e.g. on logout)."""

    __tablename__ = "token_blocklist"

    id = db.Column(db.Integer, primary_key=True)
    jti = db.Column(db.String(36), nullable=False, unique=True, index=True)
    staff_id = db.Column(db.String(20), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<TokenBlocklist {self.jti}>"
