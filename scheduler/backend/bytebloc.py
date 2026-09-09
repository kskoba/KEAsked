"""
ByteBloc REST API integration.

Reference: ByteBloc Application Programming Interface (PDF), section 10
"CREATE SHIFT REQUESTS" — the only write endpoint ByteBloc exposes.
Everything else in that document (getMainSchedule, getHoursReport, etc.)
is read-only and is intentionally NOT implemented here; this module has
exactly one job: turn validated physician preferences into ByteBloc
"OnRequest" shift-assignment requests, and — only on explicit human
confirmation — POST them.

================================================================
HARD SAFETY RULE — DO NOT REMOVE OR WEAKEN THIS
================================================================
This module must never contact ByteBloc's write endpoint
(createShiftRequests) without an explicit, in-the-moment human
confirmation. There is no automatic, scheduled, or background call
path to send_shift_requests(). Every caller — present or future —
MUST collect the literal confirmation phrase from a human (typed into
a confirmation dialog, not pre-filled or remembered) and pass it
through unchanged. send_shift_requests() re-checks that phrase itself
and refuses to run without it, so even a caller that forgets to gate
its own UI cannot accidentally push data to ByteBloc.
================================================================
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from scheduler.backend.models import PhysicianSubmission

# The exact phrase a human must type before send_shift_requests() will run.
# Checked case-sensitively, verbatim. Do not make this configurable —
# a fixed, unambiguous phrase is the point.
CONFIRMATION_PHRASE = "CONFIRM"


def _resolve_config_dir() -> Path:
    if os.environ.get("CONFIG_DIR"):
        return Path(os.environ["CONFIG_DIR"])
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config"
    return Path(__file__).parent.parent / "config"


_DEFAULT_BYTEBLOC_CONFIG_PATH = _resolve_config_dir() / "bytebloc.yaml"


@dataclass
class ByteBlocConfig:
    """
    Organization-specific ByteBloc connection details and ID mappings.

    Loaded from scheduler/config/bytebloc.yaml, which is gitignored (like
    scheduler_config.yaml) because it holds a security token. Only
    bytebloc_template.yaml is committed.
    """

    base_url: str = "https://www.bytebloc.com/sk/Svc"
    api_version: str = "V1"
    group_code: str = ""
    location_code: str = ""
    security_token: str = ""
    # ByteBloc user ID of the requester. Per the API doc, this user must
    # have "Edit All" or "location administrator" privilege for the
    # location — obtained from a getUserDetails call (read-only, done
    # once during setup, not by this app).
    requester_id: str = ""
    # KEAsked shift code (e.g. "0600h RAH A side") -> ByteBloc {SiteId, ShiftId}.
    # Obtained from a getShiftDetails call (read-only, done once during
    # setup, not by this app).
    shift_map: dict[str, dict[str, str]] = field(default_factory=dict)
    # KEAsked physician_id -> ByteBloc ProviderId.
    # Obtained from a getUserDetails call (read-only, done once during
    # setup, not by this app).
    provider_map: dict[str, str] = field(default_factory=dict)


def load_bytebloc_config(path: str | Path | None = None) -> ByteBlocConfig | None:
    """
    Load scheduler/config/bytebloc.yaml. Returns None if the file doesn't
    exist yet (org hasn't set up the integration) rather than raising, so
    callers can show a friendly "not configured" state.
    """
    cfg_path = Path(path) if path else _DEFAULT_BYTEBLOC_CONFIG_PATH
    if not cfg_path.exists():
        return None
    with cfg_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    connection = data.get("connection") or {}
    return ByteBlocConfig(
        base_url=str(connection.get("base_url") or "https://www.bytebloc.com/sk/Svc").rstrip("/"),
        api_version=str(connection.get("api_version") or "V1"),
        group_code=str(connection.get("group_code") or ""),
        location_code=str(connection.get("location_code") or ""),
        security_token=str(connection.get("security_token") or ""),
        requester_id=str(connection.get("requester_id") or ""),
        shift_map={
            str(code): {"site_id": str(v.get("site_id") or ""), "shift_id": str(v.get("shift_id") or "")}
            for code, v in (data.get("shift_map") or {}).items()
        },
        provider_map={str(k): str(v) for k, v in (data.get("provider_map") or {}).items()},
    )


def is_fully_configured(config: ByteBlocConfig) -> bool:
    return bool(
        config.group_code and config.location_code and config.security_token and config.requester_id
    )


@dataclass
class RequestPreviewItem:
    physician_id: str
    physician_name: str
    day: str            # yyyy-MM-dd, for display
    shift_code: str      # KEAsked's internal code, e.g. "0600h RAH A side"


def build_shift_requests_payload(
    submissions: list[PhysicianSubmission],
    config: ByteBlocConfig,
    year: int,
    month: int,
    display_names: dict[str, str] | None = None,
) -> tuple[dict, list[str], list[RequestPreviewItem]]:
    """
    Build the createShiftRequests JSON payload (section 10 of the API doc)
    from validated physician submissions. Does NOT contact ByteBloc.

    Only explicit shift preferences (a physician's `requested_shifts` for a
    day they marked as wanting to work) become "OnRequest" assignment
    requests. Days a physician did not mark as wanted are NOT sent as
    ByteBloc "OffRequest"s: an OffRequest requires a reason (Cme, Vacation,
    Admin, Personal, Custom1-4 per the API schema) that KEAsked's
    submission data does not capture, and inventing one would put words in
    the physician's mouth. If off-request submission is wanted later, the
    submission format needs to capture a reason first.

    Returns (payload, warnings, preview_items):
      - payload: the JSON body, ready for send_shift_requests(). May have
        an empty ProviderRequests list if nothing was mappable.
      - warnings: human-readable reasons any physician or request was
        left out (missing ByteBloc provider/shift mapping, etc.).
      - preview_items: one row per request actually included, for
        rendering a review table before the user is asked to confirm.
    """
    display_names = display_names or {}
    warnings: list[str] = []
    provider_requests: list[dict] = []
    preview_items: list[RequestPreviewItem] = []

    for sub in submissions:
        name = display_names.get(sub.physician_id, sub.physician_name)
        provider_id = config.provider_map.get(sub.physician_id)
        if not provider_id:
            warnings.append(
                f"{name}: no ByteBloc provider mapping in bytebloc.yaml — skipped entirely."
            )
            continue

        shift_requests: list[dict] = []
        for day in sub.days:
            if not day.wants_to_work or not day.requested_shifts:
                continue
            for shift_code in sorted(day.requested_shifts):
                mapping = config.shift_map.get(shift_code)
                if not mapping or not mapping.get("shift_id"):
                    warnings.append(
                        f"{name}: shift {shift_code!r} on {day.date.isoformat()} has no "
                        f"ByteBloc shift mapping in bytebloc.yaml — skipped."
                    )
                    continue
                shift_requests.append({
                    "Day": day.date.strftime("%Y%m%d"),
                    "SiteId": "",       # must be blank/null for an assignment request
                    "ShiftId": mapping["shift_id"],
                    "RequestType": "OnRequest",
                    "OffType": "",
                    "OffReason": "",
                })
                preview_items.append(RequestPreviewItem(
                    physician_id=sub.physician_id,
                    physician_name=name,
                    day=day.date.isoformat(),
                    shift_code=shift_code,
                ))

        if shift_requests:
            provider_requests.append({
                "ProviderId": provider_id,
                "ShiftRequests": shift_requests,
            })

    payload = {
        "RequesterId": config.requester_id,
        "SkedStartDate": datetime.date(year, month, 1).strftime("%Y%m%d"),
        "ProviderRequests": provider_requests,
    }
    return payload, warnings, preview_items


def send_shift_requests(payload: dict, config: ByteBlocConfig, confirmation_text: str) -> dict:
    """
    POST the payload to ByteBloc's createShiftRequests endpoint.

    Refuses to run unless confirmation_text is exactly CONFIRMATION_PHRASE
    — see the module docstring. This is a hard gate, not a formality:
    do not call this function speculatively, in a retry loop without a
    fresh human confirmation, or from anything other than the one
    explicit "send" action the user triggered.
    """
    if confirmation_text != CONFIRMATION_PHRASE:
        raise PermissionError(
            f'ByteBloc send refused: confirmation text must be exactly "{CONFIRMATION_PHRASE}".'
        )
    if not is_fully_configured(config):
        raise RuntimeError("ByteBloc is not fully configured (missing group/location/token/requester).")

    url = (
        f"{config.base_url}/createShiftRequests/{config.api_version}/"
        f"{config.group_code}/{config.location_code}/{config.security_token}"
    )
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError(f"ByteBloc returned HTTP {e.code}: {raw}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach ByteBloc: {e.reason}") from e

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"Status": "Unexpected non-JSON response", "raw": raw}
