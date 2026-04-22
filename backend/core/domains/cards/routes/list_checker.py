"""List checker routes."""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from core.domains.cards.services import list_checker_export_service, list_checker_service
from core.routes.base import views


@views.route("/list-checker", methods=["GET", "POST"])
def list_checker():
    if request.method == "GET":
        return render_template("decks/list_checker.html", results=None, pasted="")

    pasted = request.form.get("card_list", "")
    results, summary, error = list_checker_service.compute_list_checker(pasted)
    if error:
        return render_template("decks/list_checker.html", results=None, pasted=pasted, error=error)

    return render_template("decks/list_checker.html", results=results, pasted=pasted, summary=summary)


@views.route("/list-checker/export", methods=["POST"], endpoint="list_checker_export_csv")
def list_checker_export_csv():
    pasted = request.form.get("card_list", "")
    results, _summary, error = list_checker_service.compute_list_checker(pasted)

    if error or not results:
        flash("Nothing to export. Paste a list and click Check first.", "warning")
        return redirect(url_for("views.list_checker"))

    return list_checker_export_service.build_list_checker_export_response(results)


__all__ = ["list_checker", "list_checker_export_csv"]
