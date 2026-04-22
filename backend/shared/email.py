"""Minimal SMTP email helper for DragonsVault.

Configure via environment variables:
  MAIL_SERVER      SMTP host (default: localhost)
  MAIL_PORT        SMTP port (default: 587)
  MAIL_USE_TLS     Use STARTTLS (default: 1)
  MAIL_USERNAME    SMTP auth username (optional)
  MAIL_PASSWORD    SMTP auth password (optional)
  MAIL_FROM        Sender address (default: noreply@dragonsvault.app)
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "server": "localhost",
    "port": 587,
    "use_tls": True,
    "from": "noreply@dragonsvault.app",
}


def _cfg(key: str, default=None):
    return os.getenv(f"MAIL_{key.upper()}", default)


def send_email(*, to: str, subject: str, text_body: str, html_body: str | None = None) -> bool:
    """Send a single email. Returns True on success, False on failure (never raises)."""
    server = _cfg("server") or _DEFAULTS["server"]
    port = int(_cfg("port") or _DEFAULTS["port"])
    use_tls_raw = _cfg("use_tls", "1")
    use_tls = str(use_tls_raw).lower() in {"1", "true", "yes", "on"}
    username = _cfg("username") or None
    password = _cfg("password") or None
    from_addr = _cfg("from") or _DEFAULTS["from"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(server, port, timeout=10) as smtp:
            if use_tls:
                smtp.starttls(context=context)
            if username and password:
                smtp.login(username, password)
            smtp.sendmail(from_addr, [to], msg.as_string())
        logger.info("Email sent to %s: %s", to, subject)
        return True
    except Exception:
        logger.exception("Failed to send email to %s: %s", to, subject)
        return False
