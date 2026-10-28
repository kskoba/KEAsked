"""
Client for the master-schedule Google Sheet integration: one spreadsheet
file per month, hand-maintained by the department, shared with the whole
group. "Push to Master Sheet" writes an approved generated schedule into
that month's file; "Open from master sheet" reads one back. A separate
person enters learner (resident) pairings by hand into the same files --
this module's write path never touches those cells, and its read path
never reads them (see google_sheets_client.write_ranges's caller in
server.py / sheets_schedule_reader.py for the row-exclusion logic itself;
this module is just the thin transport layer, same split
sked_client.py/bytebloc.py already use).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from google.auth.transport.requests import Request as _GoogleAuthRequest
from google.oauth2.credentials import Credentials as _UserCredentials
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# First real write to a shared, department-visible artifact -- same hard
# gate bytebloc.py uses in front of its one write endpoint, for the same
# reason: a wrong push here lands on a Sheet the whole group reads daily.
CONFIRMATION_PHRASE = "CONFIRM"


def _resolve_config_dir() -> Path:
    if os.environ.get("CONFIG_DIR"):
        return Path(os.environ["CONFIG_DIR"])
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config"
    return Path(__file__).parent.parent / "config"


_CONFIG_DIR = _resolve_config_dir()
_DEFAULT_CONFIG_PATH = _CONFIG_DIR / "google_sheets.yaml"


@dataclass
class GoogleSheetsConfig:
    """Loaded from scheduler/config/google_sheets.yaml, which is gitignored."""

    # "service_account" (a robot identity; needs a Shared Drive to CREATE
    # files, since service accounts have no Drive storage of their own) or
    # "oauth" (KEAsked signs in as a real Google account once, via the
    # browser, and acts as it -- files it creates are owned by that account
    # and live in its My Drive, so a plain Gmail account works end to end).
    auth: str = "service_account"
    service_account_key_path: str = ""
    oauth_client_secrets_path: str = "google_oauth_client.json"
    oauth_token_path: str = "google_oauth_token.json"
    drive_folder_id: str = ""
    file_name_pattern: str = "{year}-{month:02d} - {month_name} {year} RAH NECHC Master Schedule"


def load_google_sheets_config(path: str | Path | None = None) -> GoogleSheetsConfig | None:
    """Returns None if google_sheets.yaml doesn't exist yet, so callers can show a friendly "not configured" state."""
    cfg_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        return None
    with cfg_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    auth = str(data.get("auth") or "service_account").strip().lower()
    if auth not in ("service_account", "oauth"):
        raise GoogleSheetsApiError(f"google_sheets.yaml: auth must be 'service_account' or 'oauth', not {auth!r}")
    return GoogleSheetsConfig(
        auth=auth,
        service_account_key_path=str(data.get("service_account_key_path") or ""),
        oauth_client_secrets_path=str(data.get("oauth_client_secrets_path") or "google_oauth_client.json"),
        oauth_token_path=str(data.get("oauth_token_path") or "google_oauth_token.json"),
        drive_folder_id=str(data.get("drive_folder_id") or ""),
        file_name_pattern=str(
            data.get("file_name_pattern")
            or "{year}-{month:02d} - {month_name} {year} RAH NECHC Master Schedule"
        ),
    )


def is_fully_configured(config: GoogleSheetsConfig) -> bool:
    """Everything needed to make API calls is in place (for oauth: signed in, token saved)."""
    if not config.drive_folder_id:
        return False
    if config.auth == "oauth":
        return _in_config_dir(config.oauth_client_secrets_path).exists() and _in_config_dir(config.oauth_token_path).exists()
    return bool(config.service_account_key_path and _key_path(config).exists())


def status_detail(config: GoogleSheetsConfig | None) -> str:
    """One line for the UI explaining why the integration is (not) usable."""
    if config is None:
        return "Not configured — copy scheduler/config/google_sheets_template.yaml to google_sheets.yaml."
    if not config.drive_folder_id:
        return "google_sheets.yaml has no drive_folder_id."
    if config.auth == "oauth":
        if not _in_config_dir(config.oauth_client_secrets_path).exists():
            return f"OAuth client file missing: {_in_config_dir(config.oauth_client_secrets_path)}"
        if not _in_config_dir(config.oauth_token_path).exists():
            return "Not signed in to Google yet — run scripts/google-oauth-login.py on this machine."
        return "Signed in (OAuth)."
    if not _key_path(config).exists():
        return f"Service account key missing: {_key_path(config)}"
    return "Service account configured."


class GoogleSheetsApiError(RuntimeError):
    pass


def _in_config_dir(rel_or_abs: str) -> Path:
    p = Path(rel_or_abs)
    return p if p.is_absolute() else _CONFIG_DIR / p


def _key_path(config: GoogleSheetsConfig) -> Path:
    return _in_config_dir(config.service_account_key_path)


def _oauth_credentials(config: GoogleSheetsConfig) -> _UserCredentials:
    token_path = _in_config_dir(config.oauth_token_path)
    if not token_path.exists():
        raise GoogleSheetsApiError(
            "Not signed in to Google yet. Run scripts/google-oauth-login.py on this machine "
            f"(it opens a browser once and saves the sign-in to {token_path})."
        )
    try:
        creds = _UserCredentials.from_authorized_user_file(str(token_path), scopes=_SCOPES)
    except (ValueError, OSError) as exc:
        raise GoogleSheetsApiError(f"Could not read the Google sign-in at {token_path}: {exc}") from exc
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(_GoogleAuthRequest())
            except Exception as exc:  # noqa: BLE001 -- google.auth raises several types
                raise GoogleSheetsApiError(
                    f"Google sign-in could not be refreshed ({exc}). Run scripts/google-oauth-login.py again."
                ) from exc
            token_path.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise GoogleSheetsApiError("Google sign-in is no longer valid. Run scripts/google-oauth-login.py again.")
    return creds


def run_oauth_login(config: GoogleSheetsConfig) -> str:
    """
    Interactive, once per machine: open the browser, sign in as the Google
    account that should own the master sheets, save the token. Returns the
    signed-in account's email. Needs a browser on this machine -- on a
    headless backend (Unraid) run it on a desktop and sync the token file
    over with scripts/sync-config-to-unraid.sh instead.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    secrets = _in_config_dir(config.oauth_client_secrets_path)
    if not secrets.exists():
        raise GoogleSheetsApiError(
            f"OAuth client file not found at {secrets} -- download the Desktop-app OAuth client JSON "
            "from Google Cloud Console (APIs & Services -> Credentials) and save it there."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), scopes=_SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    token_path = _in_config_dir(config.oauth_token_path)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    about = build("drive", "v3", credentials=creds, cache_discovery=False).about().get(fields="user(emailAddress)").execute()
    return about.get("user", {}).get("emailAddress", "")


def _get_credentials(config: GoogleSheetsConfig):
    if config.auth == "oauth":
        return _oauth_credentials(config)
    key_path = _key_path(config)
    if not key_path.exists():
        raise GoogleSheetsApiError(
            f"Service account key not found at {key_path} -- see "
            "google_sheets_template.yaml's setup steps."
        )
    return Credentials.from_service_account_file(str(key_path), scopes=_SCOPES)


def _sheets_service(config: GoogleSheetsConfig):
    return build("sheets", "v4", credentials=_get_credentials(config), cache_discovery=False)


def _drive_service(config: GoogleSheetsConfig):
    return build("drive", "v3", credentials=_get_credentials(config), cache_discovery=False)


def month_file_name(config: GoogleSheetsConfig, year: int, month: int) -> str:
    import calendar as _cal

    return config.file_name_pattern.format(
        year=year, month=month, month_name=_cal.month_name[month]
    )


def find_month_file_id(config: GoogleSheetsConfig, year: int, month: int) -> str | None:
    """Search the configured Drive folder for that month's spreadsheet file. None if not found."""
    name = month_file_name(config, year, month)
    escaped = name.replace("'", "\\'")
    query = (
        f"'{config.drive_folder_id}' in parents and name = '{escaped}' "
        "and trashed = false and mimeType = 'application/vnd.google-apps.spreadsheet'"
    )
    try:
        res = (
            _drive_service(config)
            .files()
            .list(q=query, fields="files(id, name)", pageSize=5,
                  supportsAllDrives=True, includeItemsFromAllDrives=True)
            .execute()
        )
    except HttpError as exc:
        raise GoogleSheetsApiError(f"Could not search Drive for {name!r}: {exc}") from exc
    files = res.get("files", [])
    if not files:
        return None
    return files[0]["id"]


def create_month_file(
    config: GoogleSheetsConfig, year: int, month: int, copy_from_file_id: str | None = None
) -> str:
    """
    Create that month's spreadsheet file in the configured Drive folder.
    If copy_from_file_id is given, copies that file (e.g. the previous
    month's) first so headers/formatting carry over -- the caller is
    responsible for clearing any stale physician-assignment cells
    afterward via write_ranges; this function never clears data itself.
    """
    name = month_file_name(config, year, month)
    drive = _drive_service(config)
    try:
        if copy_from_file_id:
            res = (
                drive.files()
                .copy(
                    fileId=copy_from_file_id,
                    body={"name": name, "parents": [config.drive_folder_id]},
                    fields="id",
                    supportsAllDrives=True,
                )
                .execute()
            )
            return res["id"]

        # Create the spreadsheet directly inside the target folder via the
        # Drive API. (spreadsheets().create() would land it in the service
        # account's own Drive root first -- and a service account has no
        # storage of its own, so that step fails outright. Creating in place
        # works on a Shared Drive, where the drive supplies the storage.)
        created = (
            drive.files()
            .create(
                body={
                    "name": name,
                    "mimeType": "application/vnd.google-apps.spreadsheet",
                    "parents": [config.drive_folder_id],
                },
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        return created["id"]
    except HttpError as exc:
        raise GoogleSheetsApiError(f"Could not create spreadsheet {name!r}: {exc}") from exc


def read_range(config: GoogleSheetsConfig, spreadsheet_id: str, a1_range: str) -> list[list]:
    try:
        res = (
            _sheets_service(config)
            .spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=a1_range)
            .execute()
        )
    except HttpError as exc:
        raise GoogleSheetsApiError(f"Could not read range {a1_range!r}: {exc}") from exc
    return res.get("values", [])


def write_ranges(config: GoogleSheetsConfig, spreadsheet_id: str, data: list[dict]) -> int:
    """
    data: [{"range": a1_range, "values": [[...]]}, ...] -- one entry per
    cell or small block. Callers (see server.py's
    _push_result_to_master_sheet) are responsible for never including a
    learner-row range here; this function writes exactly what it's given,
    nothing more.

    Returns the number of cells written.
    """
    if not data:
        return 0
    try:
        res = (
            _sheets_service(config)
            .spreadsheets()
            .values()
            .batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": "RAW", "data": data},
            )
            .execute()
        )
    except HttpError as exc:
        raise GoogleSheetsApiError(f"Could not write to spreadsheet {spreadsheet_id!r}: {exc}") from exc
    return res.get("totalUpdatedCells", 0)


def spreadsheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
