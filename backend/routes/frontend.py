"""Frontend shell that consumes the API endpoints."""

from flask import render_template
from flask_login import login_required

from .base import views


@views.route("/app/frontend")
@login_required
def frontend_shell():
    return render_template("frontend/index.html")


__all__ = ["frontend_shell"]
