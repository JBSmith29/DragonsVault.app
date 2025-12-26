"""Local EDHREC cache tables used for deck recommendations."""

from extensions import db


class EdhrecCommanderCard(db.Model):
    __tablename__ = "edhrec_commander_cards"

    commander_oracle_id = db.Column(db.String(36), primary_key=True)
    card_oracle_id = db.Column(db.String(36), primary_key=True)
    synergy_score = db.Column(db.Float, nullable=True)


class EdhrecCommanderTag(db.Model):
    __tablename__ = "edhrec_commander_tags"

    commander_oracle_id = db.Column(db.String(36), primary_key=True)
    tag = db.Column(db.String(120), primary_key=True)


class EdhrecTagCommander(db.Model):
    __tablename__ = "edhrec_tag_commanders"

    tag = db.Column(db.String(120), primary_key=True)
    commander_oracle_id = db.Column(db.String(36), primary_key=True)
