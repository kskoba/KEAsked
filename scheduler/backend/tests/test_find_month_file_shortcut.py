"""The month-file lookup follows Drive shortcuts (the official Schedules folder holds one shortcut per month)."""
from scheduler.backend.google_sheets_client import _SHORTCUT_MIME, _SPREADSHEET_MIME, resolve_spreadsheet_id


def test_real_spreadsheet_wins():
    files = [
        {"id": "SC", "mimeType": _SHORTCUT_MIME, "shortcutDetails": {"targetId": "T", "targetMimeType": _SPREADSHEET_MIME}},
        {"id": "SS", "mimeType": _SPREADSHEET_MIME},
    ]
    assert resolve_spreadsheet_id(files) == "SS"


def test_shortcut_to_spreadsheet_is_followed():
    files = [{"id": "SC", "mimeType": _SHORTCUT_MIME,
              "shortcutDetails": {"targetId": "TARGET", "targetMimeType": _SPREADSHEET_MIME}}]
    assert resolve_spreadsheet_id(files) == "TARGET"


def test_shortcut_to_other_type_ignored():
    files = [{"id": "SC", "mimeType": _SHORTCUT_MIME,
              "shortcutDetails": {"targetId": "F", "targetMimeType": "application/vnd.google-apps.folder"}}]
    assert resolve_spreadsheet_id(files) is None


def test_nothing_found():
    assert resolve_spreadsheet_id([]) is None
