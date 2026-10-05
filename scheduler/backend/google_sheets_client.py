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

    service_account_key_path: str = ""
    drive_folder_id: str = ""
    file_name_pattern: str = "{year}-{month:02d} - {month_name} {year} RAH NECHC Master Schedule"


def load_google_sheets_config(path: str | Path | None = None) -> GoogleSheetsConfig | None:
    """Returns None if google_sheets.yaml doesn't exist yet, so callers can show a friendly "not configured" state."""
    cfg_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        return None
    with cfg_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return GoogleSheetsConfig(
        service_account_key_path=str(data.get("service_account_key_path") or ""),
        drive_folder_id=str(data.get("drive_folder_id") or ""),
        file_name_pattern=str(
            data.get("file_name_pattern")
            or "{year}-{month:02d} - {month_name} {year} RAH NECHC Master Schedule"
        ),
    )


def is_fully_configured(config: GoogleSheetsConfig) -> bool:
    return bool(
        config.service_account_key_path
        and config.drive_folder_id
        and _key_path(config).exists()
    )


class GoogleSheetsApiError(RuntimeError):
    pass


def _key_path(config: GoogleSheetsConfig) -> Path:
    p = Path(config.service_account_key_path)
    return p if p.is_absolute() else _CONFIG_DIR / p


def _get_credentials(config: GoogleSheetsConfig) -> Credentials:
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
            .list(q=query, fields="files(id, name)", pageSize=5)
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
                )
                .execute()
            )
            return res["id"]

        sheets = _sheets_service(config)
        created = (
            sheets.spreadsheets()
            .create(body={"properties": {"title": name}}, fields="spreadsheetId")
            .execute()
        )
        file_id = created["spreadsheetId"]
        # spreadsheets().create() always lands in the service account's own
        # Drive root -- move it into the shared folder explicitly.
        existing = drive.files().get(fileId=file_id, fields="parents").execute()
        old_parents = ",".join(existing.get("parents", []))
        drive.files().update(
            fileId=file_id, addParents=config.drive_folder_id, removeParents=old_parents, fields="id"
        ).execute()
        return file_id
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
