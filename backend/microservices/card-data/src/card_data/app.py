from flask import Flask, jsonify, request
from sqlalchemy import select

from .config import load_config
from .db import ensure_tables, get_engine, get_session_factory, ping_db
from .models import OracleKeyword, OracleRole, OracleSynergy, ScryfallOracle
from .scryfall_sync import get_status, sync_scryfall


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
        except Exception as exc:
            return jsonify(status="error", error=str(exc)), 500
        finally:
            session.close()

    @app.post("/v1/scryfall/sync")
    def scryfall_sync():
        payload = request.get_json(silent=True) or {}
        force = bool(payload.get("force")) or request.args.get("force") == "1"
        try:
            result = sync_scryfall(engine, config, force=force)
        except Exception as exc:
            return jsonify(status="error", error=str(exc)), 500
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
        except Exception as exc:
            return jsonify(status="error", error=str(exc)), 500
        finally:
            session.close()

    return app
