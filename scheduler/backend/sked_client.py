"""
Client for sked, the physician shift-preference site (separate Cloudflare
Workers project, magic-link auth, no accounts). Used by the "Send Monthly
Shift Requests" action: push the roster + that month's master schedule
template, get back one signed link per physician, then this app emails
those links itself via email_sender.py's existing SMTP setup — sked never
sends email on its own.
"""

from __future__ import annotations

import base64
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml


def _resolve_config_dir() -> Path:
    if os.environ.get("CONFIG_DIR"):
        return Path(os.environ["CONFIG_DIR"])
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config"
    return Path(__file__).parent.parent / "config"


_DEFAULT_SKED_CONFIG_PATH = _resolve_config_dir() / "sked.yaml"


@dataclass
class SkedConfig:
    """Loaded from scheduler/config/sked.yaml, which is gitignored (holds a secret)."""

    base_url: str = ""
    admin_secret: str = ""
    link_days_valid: int = 21
    # sked and the annual survey are the same Worker/API reachable at two
    # Cloudflare Custom Domains -- survey links need this origin instead of
    # base_url so they resolve to the survey page, not the shift-grid one.
    # Falls back to base_url if left blank (still works via /survey).
    survey_base_url: str = ""


def load_sked_config(path: str | Path | None = None) -> SkedConfig | None:
    """Returns None if sked.yaml doesn't exist yet, so callers can show a friendly "not configured" state."""
    cfg_path = Path(path) if path else _DEFAULT_SKED_CONFIG_PATH
    if not cfg_path.exists():
        return None
    with cfg_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return SkedConfig(
        base_url=str(data.get("base_url") or "").rstrip("/"),
        admin_secret=str(data.get("admin_secret") or ""),
        link_days_valid=int(data.get("link_days_valid") or 21),
        survey_base_url=str(data.get("survey_base_url") or "").rstrip("/"),
    )


def is_fully_configured(config: SkedConfig) -> bool:
    return bool(config.base_url and config.admin_secret)


class SkedApiError(RuntimeError):
    pass


def _headers(config: SkedConfig) -> dict[str, str]:
    return {"content-type": "application/json", "x-admin-secret": config.admin_secret}


def upsert_period(config: SkedConfig, period_id: str, label: str, opens_at: str, closes_at: str) -> None:
    with httpx.Client(timeout=30) as client:
        res = client.put(
            f"{config.base_url}/api/admin/period",
            headers=_headers(config),
            json={"id": period_id, "label": label, "opensAt": opens_at, "closesAt": closes_at},
        )
    if res.status_code != 200:
        raise SkedApiError(f"Could not create/update period {period_id!r}: {res.text}")


def upload_period_template(config: SkedConfig, period_id: str, template_path: str | Path) -> None:
    template_path = Path(template_path)
    xlsx_b64 = base64.b64encode(template_path.read_bytes()).decode("ascii")
    with httpx.Client(timeout=60) as client:
        res = client.put(
            f"{config.base_url}/api/admin/period-template",
            headers=_headers(config),
            json={"periodId": period_id, "filename": template_path.name, "xlsxBase64": xlsx_b64},
        )
    if res.status_code != 200:
        raise SkedApiError(f"Could not upload template for period {period_id!r}: {res.text}")


def generate_period_links(
    config: SkedConfig, period_id: str, physicians: list[dict], base_url_override: str | None = None
) -> list[dict]:
    """
    physicians: [{"id", "name", "email" (may be ""), "maxConsecutiveShifts" (int, optional),
    "nonAcuteSitePreference" (str, optional)}, ...]. The last two seed sked's standing-preferences
    panel from the roster's existing scheduling.* fields the first time each physician is seen --
    never overwrites a preference the scheduler has already reviewed there.
    base_url_override: hit a different Custom Domain on the same Worker (e.g. config.survey_base_url
    for annual-survey links) instead of config.base_url -- the request still goes to that origin, so
    the returned URLs are on it too.
    Returns [{"id", "name", "email", "url", "hasEmail"}, ...] from sked.
    """
    base = base_url_override or config.base_url
    with httpx.Client(timeout=60) as client:
        res = client.post(
            f"{base}/api/admin/generate-period-links",
            headers=_headers(config),
            json={"periodId": period_id, "physicians": physicians, "daysValid": config.link_days_valid},
        )
    if res.status_code != 200:
        raise SkedApiError(f"Could not generate links for period {period_id!r}: {res.text}")
    return res.json()


def list_surveys(config: SkedConfig) -> list[dict]:
    """Returns [{"id", "label", "opens_at", "closes_at", "kind"}, ...] for kind='survey' periods."""
    with httpx.Client(timeout=30) as client:
        res = client.get(
            f"{config.base_url}/api/admin/periods",
            headers=_headers(config),
            params={"kind": "survey"},
        )
    if res.status_code != 200:
        raise SkedApiError(f"Could not list surveys: {res.text}")
    return res.json()


def get_survey_responses(config: SkedConfig, survey_id: str) -> list[dict]:
    """Returns [{"physicianId", "physicianName", "status", "updatedAt", "data"}, ...]."""
    with httpx.Client(timeout=30) as client:
        res = client.get(
            f"{config.base_url}/api/admin/survey-responses",
            headers=_headers(config),
            params={"surveyId": survey_id},
        )
    if res.status_code != 200:
        raise SkedApiError(f"Could not fetch responses for survey {survey_id!r}: {res.text}")
    return res.json()
