from flask import Flask, jsonify

from config import load_config
from db import get_engine, ping_db


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

    return app
