"""OpenAPI/Swagger documentation configuration."""

from __future__ import annotations

from typing import Any

from flask import Flask, jsonify, render_template_string


def init_openapi(app: Flask) -> None:
    """Initialize OpenAPI documentation endpoints."""

    @app.route("/api/docs")
    def api_docs():
        """Render Swagger UI for API documentation."""
        return render_template_string(SWAGGER_UI_TEMPLATE, spec_url="/api/openapi.json")

    @app.route("/api/openapi.json")
    def openapi_spec():
        """Return OpenAPI 3.0 specification."""
        spec = generate_openapi_spec(app)
        return jsonify(spec)


def generate_openapi_spec(app: Flask) -> dict[str, Any]:
    """Generate OpenAPI 3.0 specification from Flask routes."""
    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": "DragonsVault API",
            "description": "Magic: The Gathering collection manager API",
            "version": "1.0.0",
            "contact": {
                "name": "DragonsVault",
                "url": "https://github.com/JBSmith29/DragonsVault",
            },
            "license": {
                "name": "Unlicense",
                "url": "https://unlicense.org/",
            },
        },
        "servers": [
            {"url": "/api", "description": "API endpoints"},
        ],
        "paths": {},
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "API Token",
                },
                "cookieAuth": {
                    "type": "apiKey",
                    "in": "cookie",
                    "name": "dv_session",
                },
            },
            "schemas": {
                "Error": {
                    "type": "object",
                    "properties": {
                        "error": {"type": "string"},
                        "message": {"type": "string"},
                    },
                },
                "Card": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "oracle_id": {"type": "string"},
                        "set_code": {"type": "string"},
                        "collector_number": {"type": "string"},
                        "is_foil": {"type": "boolean"},
                        "quantity": {"type": "integer"},
                    },
                },
                "Folder": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "category": {"type": "string", "enum": ["deck", "collection"]},
                        "commander_name": {"type": "string", "nullable": True},
                        "is_public": {"type": "boolean"},
                    },
                },
                "Game": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "played_at": {"type": "string", "format": "date-time"},
                        "winner_user_id": {"type": "integer", "nullable": True},
                        "notes": {"type": "string", "nullable": True},
                    },
                },
            },
        },
        "security": [{"bearerAuth": []}, {"cookieAuth": []}],
    }

    # Auto-discover API routes
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/"):
            continue
        if rule.endpoint in ["static", "api_docs", "openapi_spec"]:
            continue

        path = rule.rule.replace("/api", "")
        if path not in spec["paths"]:
            spec["paths"][path] = {}

        for method in rule.methods:
            if method in ["HEAD", "OPTIONS"]:
                continue

            spec["paths"][path][method.lower()] = {
                "summary": f"{method} {path}",
                "operationId": f"{method.lower()}_{rule.endpoint}",
                "responses": {
                    "200": {"description": "Success"},
                    "400": {
                        "description": "Bad Request",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Error"}
                            }
                        },
                    },
                    "401": {"description": "Unauthorized"},
                    "404": {"description": "Not Found"},
                },
            }

    return spec


SWAGGER_UI_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>DragonsVault API Documentation</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
    <style>
        body { margin: 0; padding: 0; }
    </style>
</head>
<body>
    <div id="swagger-ui"></div>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-standalone-preset.js"></script>
    <script>
        window.onload = function() {
            SwaggerUIBundle({
                url: "{{ spec_url }}",
                dom_id: '#swagger-ui',
                deepLinking: true,
                presets: [
                    SwaggerUIBundle.presets.apis,
                    SwaggerUIStandalonePreset
                ],
                layout: "StandaloneLayout"
            });
        };
    </script>
</body>
</html>
"""
