"""Push to Master Sheet writes the sheet, then asks sked to sync FROM the sheet; it never pushes assignments to sked itself."""
import datetime

from scheduler.api import server
from scheduler.backend.generator import ScheduleResult, ScheduleStats


class _Cfg:
    base_url = "https://sked.test"
    admin_secret = "x"


def _result():
    return ScheduleResult(year=2027, month=1, assignments=[], unfilled=[], issues=[],
                          stats=ScheduleStats(0, 0, 0, 0, 0, 0.0, 0.0, {}, {}, None, None),
                          on_calls=[], spillover=[], spillover_on_calls=[])


def _arm(monkeypatch, sheet_ok=True):
    calls = {"sync": [], "push": []}
    monkeypatch.setitem(server._state, "result", _result())
    monkeypatch.setattr(server.google_sheets_client, "load_google_sheets_config", lambda: object())
    monkeypatch.setattr(server.google_sheets_client, "is_fully_configured", lambda c: True)
    monkeypatch.setattr(server.sked_client, "load_sked_config", lambda: _Cfg())
    monkeypatch.setattr(server.sked_client, "is_fully_configured", lambda c: True)
    monkeypatch.setattr(server.sked_client, "push_schedule", lambda *a, **k: calls["push"].append(a))
    monkeypatch.setattr(server.sked_client, "request_sheet_sync",
                        lambda c, y, m, dry_run=False: (calls["sync"].append((y, m)) or
                                                        {"status": "ok", "shifts": 630, "onCalls": 54, "learnerSlots": 45,
                                                         "unresolved": [], "periodCreated": False}))
    if sheet_ok:
        monkeypatch.setattr(server, "_push_result_to_master_sheet", lambda r, c: (684, "https://docs.google.com/x", 0))
    else:
        def boom(r, c):
            raise server.google_sheets_client.GoogleSheetsApiError("403 view only")
        monkeypatch.setattr(server, "_push_result_to_master_sheet", boom)
    return calls


def test_sheet_written_then_sked_syncs_from_it(monkeypatch):
    calls = _arm(monkeypatch)
    res = server.push_schedule_to_master_sheet(server.PushToMasterSheetRequest(confirmation_text=server.google_sheets_client.CONFIRMATION_PHRASE))
    assert res.sheet.ok
    assert res.sked.ok and "630 shifts" in res.sked.detail
    assert calls["sync"] == [(2027, 1)]
    assert calls["push"] == []


def test_sheet_failure_skips_sked(monkeypatch):
    calls = _arm(monkeypatch, sheet_ok=False)
    res = server.push_schedule_to_master_sheet(server.PushToMasterSheetRequest(confirmation_text=server.google_sheets_client.CONFIRMATION_PHRASE))
    assert not res.sheet.ok
    assert not res.sked.ok and "Skipped" in res.sked.detail
    assert calls["sync"] == [] and calls["push"] == []


def test_unmatched_names_make_the_calendar_row_fail():
    out = server._describe_sked_sync({"status": "ok", "shifts": 1, "onCalls": 0, "learnerSlots": 0, "unresolved": ["Smyth"]})
    assert not out.ok and "Smyth" in out.detail
