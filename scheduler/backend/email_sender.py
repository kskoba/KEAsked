"""
Outgoing email — physician reminder notifications (e.g. shift-request
validation deficiencies) sent from the clinic's own scheduling mailbox.

Uses plain SMTP with an app password (Gmail: myaccount.google.com/apppasswords),
not OAuth — this is a single shared sending mailbox, not per-user delegated
access, so an app password is the simplest thing that works and is what
scheduler/config/email_template.yaml walks an admin through setting up.
Nothing in the scheduler triggers a send automatically; it's always a
direct result of a user clicking "Send Reminder Email" in the app.
"""

from __future__ import annotations

import os
import smtplib
import sys
from dataclasses import dataclass
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

import yaml


def _resolve_config_dir() -> Path:
    if os.environ.get("CONFIG_DIR"):
        return Path(os.environ["CONFIG_DIR"])
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config"
    return Path(__file__).parent.parent / "config"


_DEFAULT_EMAIL_CONFIG_PATH = _resolve_config_dir() / "email.yaml"


@dataclass
class EmailConfig:
    """
    Sending-mailbox connection details, loaded from scheduler/config/email.yaml,
    which is gitignored (holds an app password) — only email_template.yaml is
    committed.
    """

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    sender_email: str = ""
    sender_password: str = ""
    # Display name on outgoing mail, e.g. "KEA Scheduling". Falls back to
    # sender_email if blank.
    sender_name: str = ""


def load_email_config(path: str | Path | None = None) -> EmailConfig | None:
    """
    Load scheduler/config/email.yaml. Returns None if the file doesn't
    exist yet (not set up) rather than raising, so callers can show a
    friendly "not configured" state.
    """
    cfg_path = Path(path) if path else _DEFAULT_EMAIL_CONFIG_PATH
    if not cfg_path.exists():
        return None
    with cfg_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    return EmailConfig(
        smtp_host=str(data.get("smtp_host") or "smtp.gmail.com"),
        smtp_port=int(data.get("smtp_port") or 587),
        sender_email=str(data.get("sender_email") or ""),
        sender_password=str(data.get("sender_password") or ""),
        sender_name=str(data.get("sender_name") or ""),
    )


def is_fully_configured(config: EmailConfig) -> bool:
    return bool(config.sender_email and config.sender_password)


def send_email(to_address: str, subject: str, body: str, config: EmailConfig) -> None:
    """
    Send a plain-text email via SMTP+STARTTLS. Raises RuntimeError with a
    human-readable reason on any failure (auth, connection, etc.) — never
    silently drops a send.
    """
    if not is_fully_configured(config):
        raise RuntimeError(
            "Email sending is not configured. Copy scheduler/config/email_template.yaml "
            "to email.yaml and fill in the sending mailbox's address and app password."
        )

    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = formataddr((config.sender_name or config.sender_email, config.sender_email))
    msg["To"] = to_address

    try:
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(config.sender_email, config.sender_password)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise RuntimeError(
            "Email login failed — the app password in email.yaml may be wrong or revoked."
        ) from e
    except (smtplib.SMTPException, OSError) as e:
        raise RuntimeError(f"Could not send email: {e}") from e
