"""Local EDHREC cache tables used for deck recommendations."""

from extensions import db


class EdhrecCommanderCard(db.Model):
    __tablename__ = "edhrec_commander_cards"

    commander_oracle_id = db.Column(db.String(36), primary_key=True)
    card_oracle_id = db.Column(db.String(36), primary_key=True)
    synergy_rank = db.Column(db.Integer, nullable=True)
    synergy_score = db.Column(db.Float, nullable=True)


class EdhrecCommanderTag(db.Model):
    __tablename__ = "edhrec_commander_tags"

    commander_oracle_id = db.Column(db.String(36), primary_key=True)
    tag = db.Column(db.String(120), primary_key=True)


class EdhrecCommanderTagCard(db.Model):
    __tablename__ = "edhrec_commander_tag_cards"

    commander_oracle_id = db.Column(db.String(36), primary_key=True)
    tag = db.Column(db.String(120), primary_key=True)
    card_oracle_id = db.Column(db.String(36), primary_key=True)
    synergy_rank = db.Column(db.Integer, nullable=True)
    synergy_score = db.Column(db.Float, nullable=True)


class EdhrecCommanderCategoryCard(db.Model):
    __tablename__ = "edhrec_commander_category_cards"

    commander_oracle_id = db.Column(db.String(36), primary_key=True)
    category = db.Column(db.String(120), primary_key=True)
    card_oracle_id = db.Column(db.String(36), primary_key=True)
    category_rank = db.Column(db.Integer, nullable=True)
    synergy_rank = db.Column(db.Integer, nullable=True)
    synergy_score = db.Column(db.Float, nullable=True)


class EdhrecCommanderTagCategoryCard(db.Model):
    __tablename__ = "edhrec_commander_tag_category_cards"

    commander_oracle_id = db.Column(db.String(36), primary_key=True)
    tag = db.Column(db.String(120), primary_key=True)
    category = db.Column(db.String(120), primary_key=True)
    card_oracle_id = db.Column(db.String(36), primary_key=True)
    category_rank = db.Column(db.Integer, nullable=True)
    synergy_rank = db.Column(db.Integer, nullable=True)
    synergy_score = db.Column(db.Float, nullable=True)


class EdhrecTagCommander(db.Model):
    __tablename__ = "edhrec_tag_commanders"

    tag = db.Column(db.String(120), primary_key=True)
    commander_oracle_id = db.Column(db.String(36), primary_key=True)


class EdhrecMetadata(db.Model):
    __tablename__ = "edhrec_metadata"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=False)
