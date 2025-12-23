from __future__ import annotations

from datetime import datetime, timezone

from flask import Flask, jsonify, request
from sqlalchemy import select

from config import load_config
from db import ensure_tables, get_engine, get_session_factory, ping_db
from models import PrintPrice
from mtgjson_client import MtgJsonClient, MtgJsonError
from price_normalizer import normalize_prices


def _is_expired(record: PrintPrice, ttl_seconds: int) -> bool:
    if ttl_seconds <= 0:
        return True
    fetched_at = record.fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - fetched_at
    return age.total_seconds() > ttl_seconds


def _record_payload(record: PrintPrice, cache_hit: bool) -> dict:
    return {
        "status": "ok",
        "scryfall_id": record.scryfall_id,
        "uuid": record.mtgjson_uuid,
        "prices": record.normalized_prices or {},
        "source": record.source or "mtgjson",
        "as_of": record.price_date,
        "fetched_at": record.fetched_at.isoformat() if record.fetched_at else None,
        "cache_hit": cache_hit,
    }


def create_app() -> Flask:
    config = load_config()
    app = Flask(__name__)
    engine = get_engine(config)
    session_factory = get_session_factory(config)
    client = MtgJsonClient(config)

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", service=config.service_name, schema=config.database_schema)

    @app.get("/readyz")
    def readyz():
        try:
            ping_db(engine, config.database_schema)
        except Exception:
            return jsonify(status="error", service=config.service_name), 503
        return jsonify(status="ready", service=config.service_name)

    @app.get("/v1/ping")
    def ping():
        return jsonify(status="ok", service=config.service_name)

    @app.get("/v1/prices/<scryfall_id>")
    def prices_for_scryfall(scryfall_id: str):
        scryfall_id = (scryfall_id or "").strip()
        if not scryfall_id:
            return jsonify(status="error", error="missing_scryfall_id"), 400

        force = request.args.get("force") == "1"

        session = session_factory()
        try:
            ensure_tables(engine)
            record = session.execute(
                select(PrintPrice).where(PrintPrice.scryfall_id == scryfall_id)
            ).scalar_one_or_none()

            if record and not force and not _is_expired(record, config.cache_ttl_seconds):
                return jsonify(_record_payload(record, True))

            if not client.has_token():
                return jsonify(status="error", error="missing_mtgjson_token"), 400

            card = client.fetch_card_by_scryfall_id(scryfall_id)
            if not card:
                return jsonify(status="not_found"), 404

            prices = client.fetch_prices_for_uuid(card["uuid"])
            normalized, as_of = normalize_prices(
                prices, config.provider_preference, config.list_type_preference
            )

            now = datetime.now(timezone.utc)
            if record is None:
                record = PrintPrice(
                    scryfall_id=scryfall_id,
                    mtgjson_uuid=card["uuid"],
                    set_code=card.get("setCode"),
                    collector_number=card.get("number"),
                    normalized_prices=normalized,
                    raw_prices=prices,
                    price_date=as_of,
                    source="mtgjson",
                    fetched_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                record.mtgjson_uuid = card["uuid"]
                record.set_code = card.get("setCode")
                record.collector_number = card.get("number")
                record.normalized_prices = normalized
                record.raw_prices = prices
                record.price_date = as_of
                record.source = "mtgjson"
                record.fetched_at = now

            session.commit()
            return jsonify(_record_payload(record, False))

        except MtgJsonError as exc:
            session.rollback()
            return jsonify(status="error", error=str(exc)), 502
        except Exception as exc:
            session.rollback()
            return jsonify(status="error", error=str(exc)), 500
        finally:
            session.close()

    return app
