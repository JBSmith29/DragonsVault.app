"""User-account CLI command registration."""

from __future__ import annotations

import click
from sqlalchemy import func

from extensions import db
from models import User


def register_user_cli_commands(app) -> None:
    @app.cli.group("users")
    def users_cli():
        """Manage DragonsVault user accounts."""

    @users_cli.command("create")
    @click.argument("username")
    @click.argument("email")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    @click.option("--display-name", default=None, help="Optional label shown in the UI")
    @click.option("--admin/--no-admin", default=False, help="Grant admin rights")
    def create_user(username, email, password, display_name, admin):
        normalized = email.strip().lower()
        if not normalized:
            raise click.ClickException("Email is required.")
        if User.query.filter(func.lower(User.email) == normalized).first():
            raise click.ClickException(f"User {normalized} already exists.")
        username_clean = username.strip().lower()
        if not username_clean:
            raise click.ClickException("Username is required.")
        if User.query.filter(func.lower(User.username) == username_clean).first():
            raise click.ClickException(f"Username {username_clean} already exists.")
        user = User(email=normalized, username=username_clean, display_name=display_name)
        user.set_password(password)
        user.is_admin = admin
        db.session.add(user)
        db.session.commit()
        click.echo(f"Created user {normalized}/{username_clean} (admin={admin}).")

    @users_cli.command("set-password")
    @click.argument("email")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def set_user_password(email, password):
        normalized = email.strip().lower()
        user = User.query.filter(func.lower(User.email) == normalized).first()
        if not user:
            raise click.ClickException(f"User {normalized} not found.")
        user.set_password(password)
        db.session.commit()
        click.echo(f"Password updated for {normalized}.")

    @users_cli.command("token")
    @click.argument("email")
    @click.option("--revoke", is_flag=True, help="Revoke the current token instead of issuing a new one")
    def manage_user_token(email, revoke):
        normalized = email.strip().lower()
        user = User.query.filter(func.lower(User.email) == normalized).first()
        if not user:
            raise click.ClickException(f"User {normalized} not found.")
        if revoke:
            user.clear_api_token()
            db.session.commit()
            click.echo("API token revoked.")
            return
        token = user.issue_api_token()
        db.session.commit()
        click.echo("New API token (store securely; shown once):")
        click.echo(token)
