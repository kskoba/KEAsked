"""
ByteBloc REST API integration.

Reference: ByteBloc Application Programming Interface (PDF), section 10
"CREATE SHIFT REQUESTS" — the only write endpoint ByteBloc exposes; every
other function here (getMainSchedule, getUserDetails, getShiftDetails)
is one of ByteBloc's read-only GET services, safe to call any time using
only read_security_token. This module's real job is turning validated
physician submissions into ByteBloc "OffRequest"/"NeedOff" entries (one
per shift a physician marked unavailable for), and — only on explicit
human confirmation — POSTing them; the read-only helpers exist to
build/verify the shift_map and provider_map config values that job
depends on. See build_shift_requests_payload's own docstring for why
this sends Off requests rather than On requests for now.

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

import csv
import datetime
import io
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

    # ByteBloc versions each service independently -- there is no single
    # "API version" for the connection as a whole. Confirmed per-service:
    # getMainSchedule=V6, getUserDetails=V2, getShiftDetails=V1,
    # createShiftRequests=V1. Each function below defaults to its own
    # confirmed version rather than reading one from here.
    base_url: str = "https://www.bytebloc.com/sk/Svc"
    group_code: str = ""
    location_code: str = ""
    # ByteBloc issues separate tokens per permission level -- read_security_token
    # only authorizes the read-only GET services (getMainSchedule, getUserDetails,
    # getShiftDetails, etc.); write_security_token is required by the one write
    # endpoint (createShiftRequests) and is deliberately a distinct field so a
    # read-only exploration call can never accidentally carry write credentials.
    # Either may be blank independently -- e.g. only read_security_token set
    # while write access is being held back on purpose.
    read_security_token: str = ""
    write_security_token: str = ""
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
        group_code=str(connection.get("group_code") or ""),
        location_code=str(connection.get("location_code") or ""),
        read_security_token=str(connection.get("read_security_token") or ""),
        write_security_token=str(connection.get("write_security_token") or ""),
        requester_id=str(connection.get("requester_id") or ""),
        shift_map={
            str(code): {"site_id": str(v.get("site_id") or ""), "shift_id": str(v.get("shift_id") or "")}
            for code, v in (data.get("shift_map") or {}).items()
        },
        provider_map={str(k): str(v) for k, v in (data.get("provider_map") or {}).items()},
    )


def is_fully_configured(config: ByteBlocConfig) -> bool:
    """Write access -- required before build_shift_requests_payload/send_shift_requests."""
    return bool(
        config.group_code and config.location_code and config.write_security_token and config.requester_id
    )


def is_read_configured(config: ByteBlocConfig) -> bool:
    """Read-only access -- required before get_main_schedule or similar GET calls."""
    return bool(config.group_code and config.location_code and config.read_security_token)


def _http_get(url: str) -> str:
    """Shared GET + error handling for the read-only helpers below. Returns the raw response body."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ByteBloc returned HTTP {e.code}: {raw}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach ByteBloc: {e.reason}") from e


def _parse_csv(raw: str) -> list[dict]:
    # CSV services still report errors (e.g. the 10-minutes-per-endpoint rate
    # limit) as a JSON {"Status": "..."} body instead of CSV -- without this
    # check that gets silently mis-parsed as garbage CSV rows.
    stripped = raw.lstrip()
    if stripped.startswith("{"):
        try:
            err = json.loads(stripped)
            raise RuntimeError(f"ByteBloc error: {err.get('Status', stripped[:300])}")
        except json.JSONDecodeError:
            pass  # not actually JSON -- fall through and try as CSV
    return list(csv.DictReader(io.StringIO(raw)))


# Confirmed per-service API versions (2026-09-23, from ByteBloc support's own
# sample URLs and follow-up) -- do not assume these are interchangeable or
# that a newer service shares an older one's version.
_GET_MAIN_SCHEDULE_VERSION = "V6"
_GET_USER_DETAILS_VERSION = "V2"
_GET_SHIFT_DETAILS_VERSION = "V1"


def get_main_schedule(config: ByteBlocConfig, sked_date: str | None = None, api_version: str | None = None) -> dict:
    """
    Fetch the published main schedule (API doc section 3) as JSON -- read-only,
    uses read_security_token only. sked_date selects which schedule period to
    return (any of "dd-MMM-yyyy", "yyyyMMdd", or "yyyy-MM-dd" -- omit for the
    period containing today).
    """
    if not is_read_configured(config):
        raise RuntimeError("ByteBloc read access is not configured (missing group/location/read token).")

    path = f"{config.group_code}/{config.location_code}/{config.read_security_token}"
    if sked_date:
        path += f"/{sked_date}"
    version = api_version or _GET_MAIN_SCHEDULE_VERSION
    url = f"{config.base_url}/getMainSchedule/{version}/{path}?format=JSON"

    raw = _http_get(url)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"ByteBloc returned non-JSON response: {raw[:500]}") from e


def get_user_details(
    config: ByteBlocConfig, providers_only: bool = True, api_version: str | None = None
) -> list[dict]:
    """
    Fetch all users/providers for this location (API doc section 8) -- read-only,
    uses read_security_token only. Returns one dict per CSV row (UserGroupId,
    LocationId, UserId, ProviderId, FirstName, LastName, Email, ... -- see the
    API doc for the full column list). providers_only=False includes non-provider
    users too (e.g. location administrators), useful for finding a requester_id.
    """
    if not is_read_configured(config):
        raise RuntimeError("ByteBloc read access is not configured (missing group/location/read token).")

    version = api_version or _GET_USER_DETAILS_VERSION
    flag = "Y" if providers_only else "F"
    url = (
        f"{config.base_url}/getUserDetails/{version}/{config.group_code}/{config.location_code}/"
        f"{config.read_security_token}?providersonly={flag}"
    )
    return _parse_csv(_http_get(url))


def get_shift_details(config: ByteBlocConfig, api_version: str | None = None) -> list[dict]:
    """
    Fetch all sites and shifts for this location (API doc section 9) -- read-only,
    uses read_security_token only. Returns one dict per CSV row (SiteId,
    SiteAbbreviation, ShiftId, ShiftShortName, ShiftStartTime, ShiftEndTime, ...).
    """
    if not is_read_configured(config):
        raise RuntimeError("ByteBloc read access is not configured (missing group/location/read token).")

    version = api_version or _GET_SHIFT_DETAILS_VERSION
    url = (
        f"{config.base_url}/getShiftDetails/{version}/"
        f"{config.group_code}/{config.location_code}/{config.read_security_token}"
    )
    return _parse_csv(_http_get(url))


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

    Sends "OffRequest"/"NeedOff" for every (day, shift) a physician's
    submission does NOT list in that day's requested_shifts -- i.e. every
    shift slot they marked unavailable, one request per slot (not a
    blanket day-level off) so a physician available for only part of a
    day is represented correctly. OffReason is left blank: a spot-check of
    ByteBloc's current setup (2026-09-23) found no existing Off requests
    there have one set despite the API doc listing it as part of the
    required schema shape, so this matches how the org already uses the
    system -- revisit if a real send ever comes back with an
    OffReason-related error status.

    Explicit "OnRequest" assignment requests (a physician's *preferred*
    shifts, sked's grid "preferred"/starred state) are NOT sent yet --
    nothing currently reads that state out of a submission. Wiring it in
    is a deliberate follow-up once that data is actually captured/used
    somewhere, not done here.

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

    # Warn once per unmapped shift code, not once per (physician, day) it's
    # hit on -- off-requests are checked against every shift code for every
    # day, so a single missing mapping would otherwise flood warnings.
    unmapped_codes = sorted(
        code for code, mapping in config.shift_map.items() if not mapping.get("shift_id")
    )
    for code in unmapped_codes:
        warnings.append(f"Shift {code!r} has no ByteBloc shift_id in bytebloc.yaml — skipped for everyone.")
    mapped_shift_codes = sorted(
        code for code, mapping in config.shift_map.items() if mapping.get("shift_id")
    )

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
            for shift_code in mapped_shift_codes:
                if shift_code in day.requested_shifts:
                    continue  # marked available -- nothing to request off
                shift_id = config.shift_map[shift_code]["shift_id"]
                shift_requests.append({
                    "Day": day.date.strftime("%Y%m%d"),
                    "SiteId": "",
                    "ShiftId": shift_id,
                    "RequestType": "OffRequest",
                    "OffType": "NeedOff",
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
        f"{config.base_url}/createShiftRequests/V1/"
        f"{config.group_code}/{config.location_code}/{config.write_security_token}"
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
