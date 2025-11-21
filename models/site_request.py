from __future__ import annotations

from datetime import datetime

from extensions import db


class SiteRequest(db.Model):
    __tablename__ = "site_requests"

    TYPE_BUG = "bug"
    TYPE_FEATURE = "feature"
    TYPES = (TYPE_BUG, TYPE_FEATURE)

    STATUS_NOT_STARTED = "not_started"
    STATUS_WORKING = "working"
    STATUS_COMPLETED = "completed"
    STATUSES = (STATUS_NOT_STARTED, STATUS_WORKING, STATUS_COMPLETED)

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    details = db.Column(db.Text, nullable=False)
    request_type = db.Column(
        db.String(20),
        nullable=False,
        default=TYPE_BUG,
        server_default=db.text(f"'{TYPE_BUG}'"),
        index=True,
    )
    status = db.Column(
        db.String(20),
        nullable=False,
        default=STATUS_NOT_STARTED,
        server_default=db.text(f"'{STATUS_NOT_STARTED}'"),
        index=True,
    )
    requester_name = db.Column(db.String(120), nullable=True)
    requester_email = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def status_label(self) -> str:
        labels = {
            self.STATUS_NOT_STARTED: "Not started",
            self.STATUS_WORKING: "Working",
            self.STATUS_COMPLETED: "Completed",
        }
        return labels.get(self.status, self.status)

    @property
    def type_label(self) -> str:
        labels = {
            self.TYPE_BUG: "Bug",
            self.TYPE_FEATURE: "Feature",
        }
        return labels.get(self.request_type, self.request_type)
