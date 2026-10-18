"""
A physician's per-month comment (sked `submissions.comment`, migration 0010)
travels from the sked listing row onto the PhysicianSubmission and out to
the Validate page row as `comment` (None when they left none). The xlsx has
no cell for it, so it must come from the listing, not the parsed sheet.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from scheduler.api import server
from scheduler.backend import sked_client
from scheduler.backend.config import PhysicianConfig
from scheduler.backend.models import DayAvailability, PhysicianSubmission


def _roster():
    return {
        "Alpha": PhysicianConfig(id="Alpha", name="Alpha", active=True),
        "Beta": PhysicianConfig(id="Beta", name="Beta", active=True),
        "Gamma": PhysicianConfig(id="Gamma", name="Gamma", active=True),
    }


ROWS = [
    {"physicianId": "Alpha", "physicianName": "Alpha", "status": "submitted", "updatedAt": "",
     "comment": "  Happy to take an extra night this month.\nPlease keep the 14th-16th clear.  "},
    {"physicianId": "Beta", "physicianName": "Beta", "status": "submitted", "updatedAt": "", "comment": ""},
    # Gamma: a sked older than migration 0010 sends no "comment" key at all.
    {"physicianId": "Gamma", "physicianName": "Gamma", "status": "submitted", "updatedAt": ""},
]


def _fake_import(path, year, month, physician_id_override=None):
    pid = physician_id_override
    days = [DayAvailability(date=date(year, month, d), wants_to_work=True, available_blocks={"0600h"})
            for d in range(1, 11)]
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=year, month=month,
                               shifts_requested=5, days=days)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(sked_client, "load_sked_config", lambda: object())
    monkeypatch.setattr(sked_client, "is_fully_configured", lambda cfg: True)
    monkeypatch.setattr(server, "load_roster", _roster)
    monkeypatch.setattr(server, "_load_scheduler_config", lambda: {})
    monkeypatch.setattr(sked_client, "list_period_submissions", lambda cfg, period_id: list(ROWS))
    monkeypatch.setattr(sked_client, "fetch_physician_export", lambda cfg, pid, period_id: b"xlsx")
    monkeypatch.setattr(server, "import_single_file", _fake_import)
    return TestClient(server.app)


def test_sked_import_carries_the_comment_onto_the_validate_row(client):
    res = client.post("/api/sked/import", json={"period_id": "2027-03", "year": 2027, "month": 3})
    assert res.status_code == 200, res.text
    by_id = {p["physician_id"]: p for p in res.json()["physicians"]}
    assert by_id["Alpha"]["comment"] == "Happy to take an extra night this month.\nPlease keep the 14th-16th clear."
    assert by_id["Beta"]["comment"] is None
    assert by_id["Gamma"]["comment"] is None
    # ...and it is on the stored submission, whitespace-trimmed.
    subs = {s.physician_id: s for s in server._state["submissions"]}
    assert subs["Alpha"].comment.startswith("Happy to take")
    assert subs["Gamma"].comment == ""


def test_combined_submissions_keep_both_comments():
    roster = {
        "Main": PhysicianConfig(id="Main", name="Main", active=True),
        "Half": PhysicianConfig(id="Half", name="Half", active=True, combined_with="Main"),
    }
    main = _fake_import(None, 2027, 3, physician_id_override="Main")
    half = _fake_import(None, 2027, 3, physician_id_override="Half")
    main.comment = "Nights only please."
    half.comment = "No Fridays."
    subs = [main, half]
    server._apply_combined_submissions(subs, roster)
    assert [s.physician_id for s in subs] == ["Main"]
    assert main.comment == "Main: Nights only please.\n\nHalf: No Fridays."

    # Only the secondary half commented: it is kept verbatim.
    main2 = _fake_import(None, 2027, 3, physician_id_override="Main")
    half2 = _fake_import(None, 2027, 3, physician_id_override="Half")
    half2.comment = "No Fridays."
    subs2 = [main2, half2]
    server._apply_combined_submissions(subs2, roster)
    assert main2.comment == "No Fridays."


def _sheet_with_a2(tmp_path, a2):
    """A minimal template (same shape test_importer_day_columns uses) with cell A2 set."""
    import openpyxl
    from scheduler.backend import importer
    path = tmp_path / "Alpha - March 2027.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Sheet1"
    ws.cell(row=1, column=1, value="Alpha")
    if a2 is not None:
        ws.cell(row=importer._COMMENT_ROW, column=importer._COMMENT_COL, value=a2)
    ws.cell(row=importer._DATE_ROW, column=1, value="DATE")
    ws.cell(row=importer._Z_ROW, column=1, value="Service Days")
    for i in range(31):
        ws.cell(row=importer._DATE_ROW, column=importer._FIRST_DAY_COL + i, value=i + 1)
    first_shift_row = min(importer._ROW_TO_SHIFT_CODE)
    for i in range(10):
        ws.cell(row=first_shift_row, column=importer._FIRST_DAY_COL + i, value="x")
    ws.cell(row=importer._N_SHIFTS_ROW, column=importer._N_SHIFTS_COL, value=6)
    wb.save(path)
    return path


def test_importer_reads_the_comment_from_cell_a2(tmp_path):
    from scheduler.backend.importer import import_single_file
    sub = import_single_file(_sheet_with_a2(tmp_path, "  =SUM(1) looks like a formula but is my note  "), 2027, 3)
    assert sub.comment == "=SUM(1) looks like a formula but is my note"
    assert sub.physician_name == "Alpha"           # A1 untouched by the A2 read
    assert import_single_file(_sheet_with_a2(tmp_path, None), 2027, 3).comment == ""


def test_sked_listing_comment_wins_but_a2_is_the_fallback(monkeypatch):
    rows = [
        {"physicianId": "Alpha", "physicianName": "Alpha", "status": "submitted", "updatedAt": "", "comment": "From listing"},
        {"physicianId": "Beta", "physicianName": "Beta", "status": "submitted", "updatedAt": ""},  # pre-0010 listing
    ]
    monkeypatch.setattr(sked_client, "list_period_submissions", lambda cfg, period_id: rows)
    monkeypatch.setattr(sked_client, "fetch_physician_export", lambda cfg, pid, period_id: b"xlsx")

    def fake_import(path, year, month, physician_id_override=None):
        sub = _fake_import(path, year, month, physician_id_override)
        sub.comment = f"From A2 of {physician_id_override}"
        return sub
    monkeypatch.setattr(server, "import_single_file", fake_import)

    subs, _not_submitted, errors = server._fetch_sked_period_submissions(object(), "2027-03", 2027, 3, _roster())
    assert errors == []
    by_id = {s.physician_id: s for s in subs}
    assert by_id["Alpha"].comment == "From listing"
    assert by_id["Beta"].comment == "From A2 of Beta"
