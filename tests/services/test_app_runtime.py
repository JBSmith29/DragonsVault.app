from pathlib import Path

from flask import Flask

from shared import app_runtime


def test_extend_csp_for_static_assets_adds_origin_once():
    app = Flask(__name__)
    app.config["STATIC_ASSET_BASE_URL"] = "https://cdn.example.com/assets/main.css"
    app.config["CONTENT_SECURITY_POLICY"] = {
        "img-src": ["'self'"],
        "script-src": "'self'",
        "style-src": ["'self'", "https://cdn.example.com"],
        "font-src": None,
    }

    app_runtime.extend_csp_for_static_assets(app)

    assert app.config["CONTENT_SECURITY_POLICY"]["img-src"] == ["'self'", "https://cdn.example.com"]
    assert app.config["CONTENT_SECURITY_POLICY"]["script-src"] == "'self' https://cdn.example.com"
    assert app.config["CONTENT_SECURITY_POLICY"]["style-src"] == ["'self'", "https://cdn.example.com"]
    assert app.config["CONTENT_SECURITY_POLICY"]["font-src"] == "https://cdn.example.com"


def test_configure_request_logging_sets_stream_and_file_handlers(tmp_path):
    app = Flask(__name__, instance_path=str(tmp_path / "instance"))
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    app_runtime.configure_request_logging(app)

    assert len(app.logger.handlers) == 2
    assert isinstance(app.logger.handlers[0].formatter, app_runtime.JsonRequestFormatter)
    assert isinstance(app.logger.handlers[1].formatter, app_runtime.JsonRequestFormatter)
    assert (Path(app.instance_path) / "logs" / "app.log").exists()
