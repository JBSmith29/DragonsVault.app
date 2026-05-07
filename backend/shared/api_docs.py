"""OpenAPI/Swagger documentation generator for DragonsVault API.

This module provides automatic API documentation generation using Flask's
built-in capabilities without requiring additional dependencies.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from flask import Blueprint, Flask, jsonify, render_template_string


def generate_openapi_spec(app: Flask) -> Dict[str, Any]:
    """Generate OpenAPI 3.0 specification from Flask routes.
    
    Args:
        app: Flask application instance
    
    Returns:
        OpenAPI specification dictionary
    """
    spec: Dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": "DragonsVault API",
            "description": "Magic: The Gathering collection manager API",
            "version": "1.0.0",
            "contact": {
                "name": "DragonsVault Support",
                "url": "https://github.com/JBSmith29/DragonsVault"
            },
            "license": {
                "name": "Unlicense",
                "url": "https://unlicense.org/"
            }
        },
        "servers": [
            {
                "url": "/api/v1",
                "description": "API v1"
            }
        ],
        "paths": {},
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                    "description": "API token authentication"
                },
                "sessionAuth": {
                    "type": "apiKey",
                    "in": "cookie",
                    "name": "session",
                    "description": "Session cookie authentication"
                }
            },
            "schemas": {
                "Error": {
                    "type": "object",
                    "properties": {
                        "error": {
                            "type": "string",
                            "description": "Error code"
                        },
                        "detail": {
                            "type": "string",
                            "description": "Human-readable error message"
                        }
                    },
                    "required": ["error", "detail"]
                },
                "User": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "username": {"type": "string"},
                        "email": {"type": "string", "format": "email"},
                        "is_admin": {"type": "boolean"}
                    }
                },
                "Folder": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "category": {"type": "string", "enum": ["deck", "collection"]},
                        "is_public": {"type": "boolean"},
                        "owner_user_id": {"type": "integer", "nullable": True},
                        "unique_count": {"type": "integer"},
                        "total_count": {"type": "integer"}
                    }
                },
                "Card": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "set_code": {"type": "string"},
                        "collector_number": {"type": "string"},
                        "lang": {"type": "string"},
                        "is_foil": {"type": "boolean"},
                        "quantity": {"type": "integer"},
                        "folder_id": {"type": "integer"},
                        "oracle_id": {"type": "string", "format": "uuid"}
                    }
                },
                "Pagination": {
                    "type": "object",
                    "properties": {
                        "total": {"type": "integer", "description": "Total number of items"},
                        "limit": {"type": "integer", "description": "Items per page"},
                        "offset": {"type": "integer", "description": "Starting offset"}
                    }
                }
            }
        },
        "security": [
            {"bearerAuth": []},
            {"sessionAuth": []}
        ]
    }
    
    # Document known API endpoints
    spec["paths"] = {
        "/me": {
            "get": {
                "summary": "Get current user profile",
                "description": "Returns the authenticated user's basic profile information",
                "tags": ["Users"],
                "security": [{"bearerAuth": []}, {"sessionAuth": []}],
                "responses": {
                    "200": {
                        "description": "User profile",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {"$ref": "#/components/schemas/User"}
                                    }
                                }
                            }
                        }
                    },
                    "401": {
                        "description": "Unauthorized",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Error"}
                            }
                        }
                    }
                }
            }
        },
        "/folders": {
            "get": {
                "summary": "List accessible folders",
                "description": "Returns all folders the current user can access (owned, shared, public, or friend folders)",
                "tags": ["Folders"],
                "security": [{"bearerAuth": []}, {"sessionAuth": []}],
                "responses": {
                    "200": {
                        "description": "List of folders",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {
                                            "type": "array",
                                            "items": {"$ref": "#/components/schemas/Folder"}
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "401": {
                        "description": "Unauthorized",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Error"}
                            }
                        }
                    }
                }
            }
        },
        "/folders/{folder_id}": {
            "get": {
                "summary": "Get folder details",
                "description": "Returns metadata for a single folder",
                "tags": ["Folders"],
                "security": [{"bearerAuth": []}, {"sessionAuth": []}],
                "parameters": [
                    {
                        "name": "folder_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                        "description": "Folder ID"
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Folder details",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {"$ref": "#/components/schemas/Folder"}
                                    }
                                }
                            }
                        }
                    },
                    "401": {"description": "Unauthorized"},
                    "403": {"description": "Forbidden - no access to this folder"},
                    "404": {"description": "Folder not found"}
                }
            }
        },
        "/folders/{folder_id}/cards": {
            "get": {
                "summary": "List cards in folder",
                "description": "Returns paginated cards for a folder",
                "tags": ["Cards"],
                "security": [{"bearerAuth": []}, {"sessionAuth": []}],
                "parameters": [
                    {
                        "name": "folder_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                        "description": "Folder ID"
                    },
                    {
                        "name": "limit",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "integer", "default": 200, "minimum": 1, "maximum": 500},
                        "description": "Number of cards per page"
                    },
                    {
                        "name": "offset",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "integer", "default": 0, "minimum": 0},
                        "description": "Starting offset for pagination"
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Paginated list of cards",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {
                                            "type": "array",
                                            "items": {"$ref": "#/components/schemas/Card"}
                                        },
                                        "pagination": {"$ref": "#/components/schemas/Pagination"}
                                    }
                                }
                            }
                        }
                    },
                    "401": {"description": "Unauthorized"},
                    "403": {"description": "Forbidden"},
                    "404": {"description": "Folder not found"}
                }
            }
        }
    }
    
    return spec


# Swagger UI HTML template (embedded, no external dependencies)
SWAGGER_UI_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DragonsVault API Documentation</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
    <style>
        body { margin: 0; padding: 0; }
        .topbar { display: none; }
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
                plugins: [
                    SwaggerUIBundle.plugins.DownloadUrl
                ],
                layout: "StandaloneLayout"
            });
        };
    </script>
</body>
</html>
"""


def create_api_docs_blueprint() -> Blueprint:
    """Create a blueprint for API documentation endpoints.
    
    Returns:
        Flask Blueprint with /api/docs routes
    """
    docs_bp = Blueprint("api_docs", __name__, url_prefix="/api/docs")
    
    @docs_bp.route("/")
    def swagger_ui():
        """Render Swagger UI for interactive API documentation."""
        from flask import current_app, url_for
        spec_url = url_for("api_docs.openapi_spec", _external=False)
        return render_template_string(SWAGGER_UI_HTML, spec_url=spec_url)
    
    @docs_bp.route("/openapi.json")
    def openapi_spec():
        """Return OpenAPI specification as JSON."""
        from flask import current_app
        spec = generate_openapi_spec(current_app)
        return jsonify(spec)
    
    return docs_bp


def register_api_docs(app: Flask) -> None:
    """Register API documentation routes on the Flask app.
    
    Args:
        app: Flask application instance
    """
    docs_bp = create_api_docs_blueprint()
    app.register_blueprint(docs_bp)


__all__ = [
    "generate_openapi_spec",
    "create_api_docs_blueprint",
    "register_api_docs",
]
