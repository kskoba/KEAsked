"""GET /api/google-sheets/compare: the header's match light. Read-only cell-by-cell comparison via locate_cells."""

from __future__ import annotations

import datetime

from fastapi.testclient import TestClient

from scheduler.api import server
from scheduler.backend.generator import Assignment, OnCallAssignment, ScheduleResult
from scheduler.backend.shifts import EXPORT_SHIFTS, SHIFT_CODE_LOOKUP

YEAR, MONTH = 2027, 2
NOC_TIME = next(t for s_, t, _, _ in EXPORT_SHIFTS if s_ == "NOC")


def _grid(names: dict) -> list[list]:
    """One real-layout week block (Sun Jan 31 .. Sat Feb 6 2027) with the given {(label, time, col_idx): name}."""
    g = [["", "SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"], ["", "", "1", "2", "3", "4", "5", "6"]]
    for site_label, time_label, _, _ in EXPORT_SHIFTS:
        row = [site_label] + [""] * 7 + [site_label]
        for c in range(7):
            row[c + 1] = names.get((site_label, time_label, c), "")
        g.append(row)
        g.append([time_label] + [""] * 7 + [time_label])
    return g


def _result() -> ScheduleResult:
    return ScheduleResult(
        year=YEAR, month=MONTH,
        assignments=[Assignment(date=datetime.date(2027, 2, 1), shift=SHIFT_CODE_LOOKUP["0600h RAH A side"], physician_id="P", physician_name="Smith, A")],
        on_calls=[OnCallAssignment(date=datetime.date(2027, 2, 1), call_type="NOC", physician_id="Q", physician_name="Jones, B")],
    )


class _Cfg:
    auth = "oauth"; drive_folder_id = "F"; oauth_client_secrets_path = "x"; oauth_token_path = "y"
    service_account_key_path = ""; file_name_pattern = "{year}-{month:02d} test"


def _stub(monkeypatch, grid, file_id="FILE"):
    monkeypatch.setitem(server._state, "result", _result())
    monkeypatch.setattr(server.google_sheets_client, "load_google_sheets_config", lambda: _Cfg())
    monkeypatch.setattr(server.google_sheets_client, "is_fully_configured", lambda c: True)
    monkeypatch.setattr(server.google_sheets_client, "find_month_file_id", lambda c, y, m: file_id)
    monkeypatch.setattr(server.google_sheets_client, "read_range", lambda c, f, r: grid)
    monkeypatch.setattr(server.google_sheets_client, "spreadsheet_url", lambda f: "https://sheet/" + f)


def test_match_when_every_covered_cell_agrees(monkeypatch):
    # Feb 1 2027 is a Monday -> column index 1
    _stub(monkeypatch, _grid({("RAH A", "0600-1200", 1): "smith,  a", ("NOC", NOC_TIME, 1): "Jones, B"}))
    body = TestClient(server.app).get("/api/google-sheets/compare").json()
    assert body["status"] == "match", body
    assert body["checked"] == len(EXPORT_SHIFTS) and body["differences"] == 0


def test_mismatch_lists_examples(monkeypatch):
    _stub(monkeypatch, _grid({("RAH A", "0600-1200", 1): "Someone Else", ("NOC", NOC_TIME, 1): "Jones, B",
                              ("RAH B", "0600-1200", 1): "Stray Name"}))
    body = TestClient(server.app).get("/api/google-sheets/compare").json()
    assert body["status"] == "mismatch" and body["differences"] == 2
    shown = {(d["shift"], d["sheet"], d["app"]) for d in body["examples"]}
    assert ("0600h RAH A side", "Someone Else", "Smith, A") in shown
    assert ("0600h RAH B side", "Stray Name", "") in shown


def test_no_sheet_and_no_schedule(monkeypatch):
    _stub(monkeypatch, [], file_id=None)
    assert TestClient(server.app).get("/api/google-sheets/compare").json()["status"] == "no_sheet"
    monkeypatch.setitem(server._state, "result", None)
    assert TestClient(server.app).get("/api/google-sheets/compare").json()["status"] == "no_schedule"


def test_missing_row_counts_as_difference(monkeypatch):
    grid = [r for r in _grid({("RAH A", "0600-1200", 1): "Smith, A", ("NOC", NOC_TIME, 1): "Jones, B"}) if r[0] not in ("NOC", NOC_TIME)]
    _stub(monkeypatch, grid)
    body = TestClient(server.app).get("/api/google-sheets/compare").json()
    assert body["status"] == "mismatch" and body["missing_rows"] == 1
