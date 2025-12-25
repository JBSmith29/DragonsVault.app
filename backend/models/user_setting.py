from __future__ import annotations

from extensions import db


class UserSetting(db.Model):
    __tablename__ = "user_settings"

    key = db.Column(db.Text, primary_key=True)
    value = db.Column(db.Text, nullable=True)
