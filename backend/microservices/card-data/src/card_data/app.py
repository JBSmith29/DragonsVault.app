from __future__ import annotations

import hmac
import os
from ipaddress import ip_address, ip_network

from flask import Flask, jsonify, request
from sqlalchemy import select

from .config import load_config
from .db import ensure_tables, get_engine, get_session_factory, ping_db
from .models import OracleKeyword, OracleRole, OracleSynergy, ScryfallOracle
from .scryfall_sync import get_status, sync_scryfall


def _parse_bool(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


_DEFAULT_SYNC_ALLOWLIST = (
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
)


def _sync_allowlist() -> tuple:
    raw = os.getenv("CARD_DATA_SYNC_ALLOWLIST")
    entries = (
        [item.strip() for item in raw.split(",") if item.strip()]
        if raw
        else list(_DEFAULT_SYNC_ALLOWLIST)
    )
    networks = []
    for entry in entries:
        try:
            networks.append(ip_network(entry, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _client_ip() -> str | None:
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.headers.get("X-Real-IP"):
        return request.headers.get("X-Real-IP")
    if request.remote_addr:
        return request.remote_addr
    return None


def _is_sync_authorized() -> bool:
    expected_token = (os.getenv("CARD_DATA_SYNC_TOKEN") or "").strip()
    provided_token = (request.headers.get("X-Card-Data-Token") or "").strip()
    if expected_token:
        return bool(provided_token) and hmac.compare_digest(provided_token, expected_token)

    client_ip = _client_ip()
    if not client_ip:
        return False
    try:
        client = ip_address(client_ip)
    except ValueError:
        return False
    return any(client in network for network in _sync_allowlist())


def _service_error(app: Flask, context: str):
    app.logger.exception("%s failed", context)
    return jsonify(status="error", error="internal_error"), 500


def create_app() -> Flask:
    config = load_config()
    app = Flask(__name__)
    engine = get_engine(config)

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", service=config.service_name, schema=config.database_schema)

    @app.get("/readyz")
    def readyz():
        try:
            ping_db(engine, config.database_schema)
        except Exception:
            return (
                jsonify(status="error", service=config.service_name),
                503,
            )
        return jsonify(status="ready", service=config.service_name)

    @app.get("/v1/ping")
    def ping():
        return jsonify(status="ok", service=config.service_name)

    @app.get("/v1/scryfall/status")
    def scryfall_status():
        session = get_session_factory(config)()
        try:
            ensure_tables(engine)
            payload = get_status(session)
            return jsonify(payload)
        except Exception:
            return _service_error(app, "scryfall status")
        finally:
            session.close()

    @app.post("/v1/scryfall/sync")
    def scryfall_sync():
        if not _is_sync_authorized():
            return jsonify(status="error", error="forbidden"), 403
        payload = request.get_json(silent=True) or {}
        force = _parse_bool(payload.get("force")) or _parse_bool(request.args.get("force"))
        try:
            result = sync_scryfall(engine, config, force=force)
        except Exception:
            return _service_error(app, "scryfall sync")
        if result.get("status") == "locked":
            return jsonify(result), 409
        return jsonify(result)

    @app.get("/v1/oracles/<oracle_id>")
    def oracle_detail(oracle_id: str):
        session = get_session_factory(config)()
        try:
            ensure_tables(engine)
            oracle = session.get(ScryfallOracle, oracle_id)
            if not oracle:
                return jsonify(status="not_found"), 404
            keywords = session.execute(
                select(OracleKeyword.keyword).where(OracleKeyword.oracle_id == oracle_id)
            ).scalars().all()
            role = session.get(OracleRole, oracle_id)
            synergies = session.execute(
                select(
                    OracleSynergy.related_oracle_id,
                    OracleSynergy.weight,
                    OracleSynergy.source,
                    OracleSynergy.notes,
                ).where(OracleSynergy.oracle_id == oracle_id)
            ).all()
            return jsonify(
                status="ok",
                oracle={
                    "oracle_id": oracle.oracle_id,
                    "name": oracle.name,
                    "type_line": oracle.type_line,
                    "oracle_text": oracle.oracle_text,
                    "mana_cost": oracle.mana_cost,
                    "cmc": oracle.cmc,
                    "colors": oracle.colors,
                    "color_identity": oracle.color_identity,
                    "legalities": oracle.legalities,
                    "layout": oracle.layout,
                    "card_faces": oracle.card_faces,
                    "edhrec_rank": oracle.edhrec_rank,
                    "power": oracle.power,
                    "toughness": oracle.toughness,
                    "loyalty": oracle.loyalty,
                    "defense": oracle.defense,
                    "scryfall_uri": oracle.scryfall_uri,
                    "created_at": oracle.created_at.isoformat(),
                    "updated_at": oracle.updated_at.isoformat(),
                },
                keywords=keywords,
                roles={
                    "primary_role": role.primary_role if role else None,
                    "roles": role.roles if role else None,
                    "subroles": role.subroles if role else None,
                },
                synergies=[
                    {
                        "related_oracle_id": row.related_oracle_id,
                        "weight": row.weight,
                        "source": row.source,
                        "notes": row.notes,
                    }
                    for row in synergies
                ],
            )
        except Exception:
            return _service_error(app, "oracle detail")
        finally:
            session.close()

    return app
