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

_SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"
_SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
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
    """
    Search the configured Drive folder for that month's spreadsheet file.
    None if not found. A Drive SHORTCUT with that name is followed to the
    spreadsheet it points at -- the department's official "Schedules"
    folder holds one shortcut per month, not the files themselves
    (confirmed 2026-10-28), so a spreadsheet-only query there finds nothing.
    """
    name = month_file_name(config, year, month)
    escaped = name.replace("'", "\\'")
    query = (
        f"'{config.drive_folder_id}' in parents and name = '{escaped}' and trashed = false "
        f"and (mimeType = '{_SPREADSHEET_MIME}' or mimeType = '{_SHORTCUT_MIME}')"
    )
    try:
        res = (
            _drive_service(config)
            .files()
            .list(q=query, fields="files(id, name, mimeType, shortcutDetails(targetId, targetMimeType))",
                  pageSize=10, supportsAllDrives=True, includeItemsFromAllDrives=True)
            .execute()
        )
    except HttpError as exc:
        raise GoogleSheetsApiError(f"Could not search Drive for {name!r}: {exc}") from exc
    return resolve_spreadsheet_id(res.get("files", []))


def resolve_spreadsheet_id(files: list[dict]) -> str | None:
    """
    The spreadsheet id among Drive search results: a real spreadsheet wins,
    else the target of a shortcut that points at a spreadsheet, else None.
    """
    for f in files:
        if f.get("mimeType") == _SPREADSHEET_MIME:
            return f["id"]
    for f in files:
        details = f.get("shortcutDetails") or {}
        if f.get("mimeType") == _SHORTCUT_MIME and details.get("targetMimeType") == _SPREADSHEET_MIME:
            return details.get("targetId") or None
    return None


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


# Look of the department's hand-built master sheet (read off the real
# December 2026 file, 2026-10-28): Arial 10, everything centred, thin
# borders on every cell, 18 px rows; call rows (and their learner row)
# amber, every other shift row light blue, labels bold; day headers and
# date numbers bold; a learner-type colour legend beside the grid.
MASTER_BLOCK_ROWS = 48          # header + date row + 23 shifts x (name row + learner row), no gap
_COL_WIDTHS_PX = {0: 79, 1: 131, 2: 131, 3: 131, 4: 131, 5: 131, 6: 131, 7: 131, 8: 85, 9: 100, 10: 131}
_ROW_HEIGHT_PX = 18
_FILL_CALL = {"red": 1.0, "green": 0.8, "blue": 0.0}            # ffcc00
_FILL_SHIFT = {"red": 0.8118, "green": 0.8863, "blue": 0.9529}  # cfe2f3
_WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}
MASTER_LEGEND: list[tuple[str, dict, bool]] = [   # (text, fill, white text)
    ("LEGEND", _WHITE, False),
    ("EM resident", {"red": 0.2902, "green": 0.5255, "blue": 0.9098}, True),
    ("Off service resident", {"red": 1.0, "green": 0.0, "blue": 0.0}, True),
    ("Elective resident", {"red": 0.6, "green": 0.0, "blue": 1.0}, True),
    ("Elective student", {"red": 1.0, "green": 0.0, "blue": 1.0}, True),
    ("Core Student", {"red": 0.0, "green": 1.0, "blue": 0.0}, False),
    ("Shadow student", {"red": 1.0, "green": 1.0, "blue": 0.0}, False),
    ("NP student", {"red": 0.851, "green": 0.8235, "blue": 0.9137}, False),
    ("NP", {"red": 0.6078, "green": 0.7333, "blue": 0.349}, False),
]
MASTER_LEGEND_COL = 10   # column K, beside the grid, rows 3..11 of the first block


def _first_sheet_id(config: GoogleSheetsConfig, spreadsheet_id: str) -> int:
    try:
        meta = (
            _sheets_service(config).spreadsheets()
            .get(spreadsheetId=spreadsheet_id, fields="sheets(properties(sheetId,index))")
            .execute()
        )
    except HttpError as exc:
        raise GoogleSheetsApiError(f"Could not read sheet properties: {exc}") from exc
    sheets = sorted(meta.get("sheets", []), key=lambda sh: sh["properties"].get("index", 0))
    if not sheets:
        raise GoogleSheetsApiError("Spreadsheet has no sheets.")
    return int(sheets[0]["properties"]["sheetId"])


def format_new_master_sheet(config: GoogleSheetsConfig, spreadsheet_id: str, n_weeks: int,
                            call_row_indexes: list[int], shift_count: int) -> int:
    """
    Apply the department's master-sheet look to a sheet KEAsked has just
    written in its block layout (MASTER_BLOCK_ROWS per week, no gap).
    call_row_indexes: positions (0-based) within the shift list that are
    on-call rows (amber). Returns the number of batchUpdate requests sent.
    """
    sid = _first_sheet_id(config, spreadsheet_id)
    total_rows = n_weeks * MASTER_BLOCK_ROWS
    base_text = {"fontFamily": "Arial", "fontSize": 10, "bold": False}
    thin = {"style": "SOLID", "width": 1, "color": {"red": 0, "green": 0, "blue": 0}}
    borders = {"top": thin, "bottom": thin, "left": thin, "right": thin}

    def rng(r0, r1, c0, c1):
        return {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}

    def repeat(r0, r1, c0, c1, fmt: dict, fields: str):
        return {"repeatCell": {"range": rng(r0, r1, c0, c1), "cell": {"userEnteredFormat": fmt}, "fields": fields}}

    reqs: list[dict] = []
    # Grid size: exactly the rows we use, like the real sheet (no trailing empty rows), K for the legend.
    reqs.append({"updateSheetProperties": {
        "properties": {"sheetId": sid, "gridProperties": {"rowCount": total_rows, "columnCount": MASTER_LEGEND_COL + 1}},
        "fields": "gridProperties.rowCount,gridProperties.columnCount"}})
    for col, px in _COL_WIDTHS_PX.items():
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": col, "endIndex": col + 1},
            "properties": {"pixelSize": px}, "fields": "pixelSize"}})
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "ROWS", "startIndex": 0, "endIndex": total_rows},
        "properties": {"pixelSize": _ROW_HEIGHT_PX}, "fields": "pixelSize"}})
    # Base: Arial 10, centred, thin borders, white, over A..I of the whole grid.
    reqs.append(repeat(0, total_rows, 0, 9,
                       {"textFormat": base_text, "horizontalAlignment": "CENTER", "borders": borders, "backgroundColor": _WHITE},
                       "userEnteredFormat(textFormat,horizontalAlignment,borders,backgroundColor)"))
    bold = {"textFormat": {"bold": True}}
    for w in range(n_weeks):
        top = w * MASTER_BLOCK_ROWS
        reqs.append(repeat(top, top + 2, 0, 9, bold, "userEnteredFormat.textFormat.bold"))  # day names + dates
        for k in range(shift_count):
            name_row = top + 2 + 2 * k
            is_call = k in call_row_indexes
            fill = _FILL_CALL if is_call else _FILL_SHIFT
            reqs.append(repeat(name_row, name_row + 1, 0, 9, {"backgroundColor": fill}, "userEnteredFormat.backgroundColor"))
            if is_call:   # the call rows' learner row is amber too
                reqs.append(repeat(name_row + 1, name_row + 2, 0, 9, {"backgroundColor": fill}, "userEnteredFormat.backgroundColor"))
            reqs.append(repeat(name_row, name_row + 1, 0, 1, bold, "userEnteredFormat.textFormat.bold"))
            reqs.append(repeat(name_row, name_row + 1, 8, 9, bold, "userEnteredFormat.textFormat.bold"))
    # Legend beside the first block.
    for i, (_text, fill, white_text) in enumerate(MASTER_LEGEND):
        r = 2 + i
        tf = {"fontFamily": "Arial", "fontSize": 11, "bold": i == 0}
        if white_text:
            tf["foregroundColor"] = _WHITE
        reqs.append(repeat(r, r + 1, MASTER_LEGEND_COL, MASTER_LEGEND_COL + 1,
                           {"textFormat": tf, "horizontalAlignment": "CENTER", "borders": borders, "backgroundColor": fill},
                           "userEnteredFormat(textFormat,horizontalAlignment,borders,backgroundColor)"))
    try:
        _sheets_service(config).spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": reqs}).execute()
    except HttpError as exc:
        raise GoogleSheetsApiError(f"Could not format the new master sheet: {exc}") from exc
    return len(reqs)


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


def read_grid_with_fills(config: GoogleSheetsConfig, spreadsheet_id: str, a1_range: str) -> tuple[list[list], list[list]]:
    """
    Like read_range but also returns each cell's effective background colour
    as a lowercase hex string ("ffcc00") or "" for none/white. Two ragged
    grids of equal shape: (values, fills). Used for the learner row under
    each shift on the master sheet, whose colour is the learner type.
    """
    try:
        res = (
            _sheets_service(config).spreadsheets()
            .get(spreadsheetId=spreadsheet_id, ranges=[a1_range], includeGridData=True,
                 fields="sheets(data(rowData(values(formattedValue,effectiveFormat(backgroundColor)))))")
            .execute()
        )
    except HttpError as exc:
        raise GoogleSheetsApiError(f"Could not read range {a1_range!r} with formatting: {exc}") from exc
    values: list[list] = []
    fills: list[list] = []
    for sheet in res.get("sheets", [])[:1]:
        for block in sheet.get("data", []):
            for row in block.get("rowData", []):
                vrow: list = []
                frow: list = []
                for cell in row.get("values", []):
                    vrow.append(cell.get("formattedValue", ""))
                    frow.append(_hex_fill(cell.get("effectiveFormat", {}).get("backgroundColor")))
                values.append(vrow)
                fills.append(frow)
    return values, fills


def _hex_fill(color: dict | None) -> str:
    if not color:
        return ""
    r, g, b = (round(float(color.get(k, 0)) * 255) for k in ("red", "green", "blue"))
    return "" if (r, g, b) == (255, 255, 255) else f"{r:02x}{g:02x}{b:02x}"


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
