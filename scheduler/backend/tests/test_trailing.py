"""
Cross-month continuity *wiring* tests -- the piece between a finalized
prior-month ScheduleResult and CpsatScheduleGenerator's trailing_assignments
parameter (whose own constraint behaviour is covered by
test_cpsat_cross_month.py).

Three layers:
  1. scheduler.backend.trailing pure helpers (window length, on-call
     exclusion, wrong-month guard, year rollover).
  2. The real server xlsx round trip: a fake December ScheduleResult ->
     _build_export_workbook -> _parse_schedule_xlsx -> build_trailing_assignments
     -> a real CP-SAT solve of January 1st, proving the whole local-file path
     actually blocks/unblocks day 1 the way the synthetic tests say it should.
  3. The /api/trailing-schedule routes + /api/generate's resolver, including
     that loading a trailing month never touches the active month's state and
     that a missing/wrong-month source degrades to None instead of failing.
"""

from __future__ import annotations

import calendar
import datetime

import pytest

from scheduler.backend import trailing as trailing_mod
from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator import Assignment, OnCallAssignment, ScheduleResult
from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES, Shift

YEAR, MONTH = 2027, 1
DEC_YEAR = YEAR - 1
DAY = Shift("0600h", "RAH A side")
NIGHT = Shift("2400h", "RAH A side")


def _dec(day: int) -> datetime.date:
    return datetime.date(DEC_YEAR, 12, day)


def _cfg(pid: str, **kw) -> PhysicianConfig:
    return PhysicianConfig(id=pid, name=pid, **kw)


def _december(assignments: list[tuple[str, int, Shift]], on_calls: list[tuple[str, int, str]] = ()) -> ScheduleResult:
    return ScheduleResult(
        year=DEC_YEAR,
        month=12,
        assignments=[
            Assignment(date=_dec(d), shift=sh, physician_id=pid, physician_name=pid)
            for pid, d, sh in assignments
        ],
        on_calls=[
            OnCallAssignment(date=_dec(d), call_type=ct, physician_id=pid, physician_name=pid)
            for pid, d, ct in on_calls
        ],
    )


def _jan1_only_submission(physician_id: str, **kwargs) -> PhysicianSubmission:
    days_in_month = calendar.monthrange(YEAR, MONTH)[1]
    days = [
        DayAvailability(
            date=datetime.date(YEAR, MONTH, d),
            wants_to_work=(d == 1),
            available_blocks=frozenset(range(5)),
            requested_shifts=frozenset(ALL_SHIFT_CODES),
        )
        for d in range(1, days_in_month + 1)
    ]
    return PhysicianSubmission(
        physician_id=physician_id, physician_name=physician_id,
        year=YEAR, month=MONTH, shifts_requested=1, shifts_min=0, shifts_max=1,
        days=days, **kwargs,
    )


def _jan1_assigned(result) -> list:
    return [a for a in result.assignments if a.date == datetime.date(YEAR, MONTH, 1)]


# --------------------------------------------------------------------------- #
# 1. Pure helpers
# --------------------------------------------------------------------------- #

def test_previous_month_rolls_year_back_in_january():
    assert trailing_mod.previous_month(2027, 1) == (2026, 12)
    assert trailing_mod.previous_month(2026, 7) == (2026, 6)


def test_window_is_max_of_caps_with_floor_of_three():
    assert trailing_mod.trailing_window_days(_cfg("a", max_consecutive_shifts=1, max_consecutive_nights=1)) == 3
    assert trailing_mod.trailing_window_days(_cfg("b", max_consecutive_shifts=5, max_consecutive_nights=2)) == 5
    assert trailing_mod.trailing_window_days(_cfg("c", max_consecutive_shifts=2, max_consecutive_nights=4)) == 4
    assert trailing_mod.trailing_window_days(None) == 3


def test_build_keeps_only_each_physicians_own_window():
    roster = {
        "Short": _cfg("Short", max_consecutive_shifts=3, max_consecutive_nights=1),
        "Long": _cfg("Long", max_consecutive_shifts=6, max_consecutive_nights=2),
    }
    dec = _december(
        [("Short", d, DAY) for d in range(20, 32)] + [("Long", d, DAY) for d in range(20, 32)]
    )
    out = trailing_mod.build_trailing_assignments(dec, roster, YEAR, MONTH)

    assert [d.day for d, _ in out["Short"]] == [29, 30, 31]
    assert [d.day for d, _ in out["Long"]] == [26, 27, 28, 29, 30, 31]
    assert all(sh == DAY for _, sh in out["Short"] + out["Long"])


def test_build_excludes_on_call_and_omits_physicians_with_nothing_in_window():
    roster = {"A": _cfg("A"), "B": _cfg("B"), "C": _cfg("C")}
    dec = _december(
        assignments=[("A", 31, NIGHT), ("B", 10, DAY)],          # B's shift is far outside the window
        on_calls=[("A", 31, "DOC"), ("C", 31, "NOC"), ("C", 30, "DOC")],  # C is on-call only
    )
    out = trailing_mod.build_trailing_assignments(dec, roster, YEAR, MONTH)

    assert set(out) == {"A"}
    assert out["A"] == [(_dec(31), NIGHT)]


def test_build_rejects_a_schedule_that_is_not_the_previous_month():
    dec = _december([("A", 31, DAY)])
    with pytest.raises(ValueError):
        trailing_mod.build_trailing_assignments(dec, {}, 2027, 3)   # March needs February, not December
    with pytest.raises(ValueError):
        trailing_mod.build_trailing_assignments(dec, {}, 2028, 1)   # wrong year


def test_build_with_unrostered_physician_uses_default_window():
    dec = _december([("Ghost", d, DAY) for d in (27, 28, 29, 30, 31)])
    out = trailing_mod.build_trailing_assignments(dec, {}, YEAR, MONTH)
    assert [d.day for d, _ in out["Ghost"]] == [29, 30, 31]


def test_summary_is_human_readable():
    assert trailing_mod.summarize_trailing({}) == "no trailing shifts"
    s = trailing_mod.summarize_trailing({"A": [(_dec(30), DAY), (_dec(31), DAY)]})
    assert "1 physician" in s and "2 regular shift" in s and "2026-12-30..2026-12-31" in s


# --------------------------------------------------------------------------- #
# 2. Real xlsx round trip through the server's own export/parse into a solve
# --------------------------------------------------------------------------- #

def _roundtrip_through_xlsx(dec: ScheduleResult, roster: dict, tmp_path) -> ScheduleResult:
    from scheduler.api import server as server_mod

    wb = server_mod._build_export_workbook(dec)
    path = tmp_path / "schedule_2026_12.xlsx"
    wb.save(path)
    return server_mod._parse_schedule_xlsx(path, roster)


def test_xlsx_roundtrip_siar_blocks_day_one_when_december_run_is_at_cap(tmp_path):
    cfg = _cfg("Test", max_consecutive_shifts=4)
    roster = {"Test": cfg}
    dec = _december([("Test", d, DAY) for d in (28, 29, 30, 31)])

    parsed = _roundtrip_through_xlsx(dec, roster, tmp_path)
    assert (parsed.year, parsed.month) == (DEC_YEAR, 12)
    trailing = trailing_mod.build_trailing_assignments(parsed, roster, YEAR, MONTH)
    assert [d.day for d, _ in trailing["Test"]] == [28, 29, 30, 31]

    gen = CpsatScheduleGenerator([_jan1_only_submission("Test")], roster, {}, trailing_assignments=trailing)
    result = gen.generate(YEAR, MONTH, time_limit=10.0, num_workers=4)
    assert _jan1_assigned(result) == []


def test_xlsx_roundtrip_siar_allows_day_one_with_headroom(tmp_path):
    cfg = _cfg("Test", max_consecutive_shifts=4)
    roster = {"Test": cfg}
    dec = _december([("Test", d, DAY) for d in (29, 30, 31)], on_calls=[("Test", 28, "DOC")])

    parsed = _roundtrip_through_xlsx(dec, roster, tmp_path)
    trailing = trailing_mod.build_trailing_assignments(parsed, roster, YEAR, MONTH)
    # The Dec 28 DOC must not have been counted as a 4th consecutive shift.
    assert [d.day for d, _ in trailing["Test"]] == [29, 30, 31]

    gen = CpsatScheduleGenerator([_jan1_only_submission("Test")], roster, {}, trailing_assignments=trailing)
    result = gen.generate(YEAR, MONTH, time_limit=10.0, num_workers=4)
    assert len(_jan1_assigned(result)) == 1


# --------------------------------------------------------------------------- #
# 3. Server routes and /api/generate's resolver
# --------------------------------------------------------------------------- #

@pytest.fixture
def server(monkeypatch):
    from scheduler.api import server as server_mod

    # Isolate module-level state so these tests neither see nor leak anything.
    saved = {k: server_mod._state[k] for k in ("result", "year", "month", "submissions", "roster",
                                               "trailing_source", "trailing_last_used")}
    server_mod._state["trailing_source"] = None
    server_mod._state["trailing_last_used"] = None
    # Never touch real Google Sheets credentials from a test.
    monkeypatch.setattr(server_mod.google_sheets_client, "load_google_sheets_config", lambda: None)
    yield server_mod
    server_mod._state.update(saved)


def test_load_trailing_route_does_not_clobber_active_month(server, tmp_path):
    from scheduler.api.schemas import LoadTrailingScheduleRequest

    roster = {"Test": _cfg("Test")}
    server._state["roster"] = roster
    active = ScheduleResult(year=YEAR, month=MONTH)
    server._state.update({"result": active, "year": YEAR, "month": MONTH})

    dec = _december([("Test", 31, DAY)])
    path = tmp_path / "dec.xlsx"
    server._build_export_workbook(dec).save(path)

    status = server.load_trailing_schedule(LoadTrailingScheduleRequest(file=str(path)))

    assert status.loaded and (status.year, status.month) == (DEC_YEAR, 12)
    assert status.source == "xlsx" and status.physician_count == 1 and status.assignment_count == 1
    # Active month untouched:
    assert server._state["result"] is active
    assert (server._state["year"], server._state["month"]) == (YEAR, MONTH)

    cleared = server.clear_trailing_schedule()
    assert not cleared.loaded and server._state["trailing_source"] is None


def test_load_trailing_route_rejects_missing_or_non_xlsx(server, tmp_path):
    from fastapi import HTTPException
    from scheduler.api.schemas import LoadTrailingScheduleRequest

    with pytest.raises(HTTPException) as e:
        server.load_trailing_schedule(LoadTrailingScheduleRequest(file=str(tmp_path / "nope.xlsx")))
    assert e.value.status_code == 400

    txt = tmp_path / "dec.txt"
    txt.write_text("x")
    with pytest.raises(HTTPException) as e:
        server.load_trailing_schedule(LoadTrailingScheduleRequest(file=str(txt)))
    assert e.value.status_code == 400


def test_resolver_degrades_to_none_with_no_source(server):
    out = server._resolve_trailing_assignments(YEAR, MONTH, {})
    assert out == (None, None)
    assert "No cross-month data" in server._state["trailing_last_used"]


def test_resolver_uses_loaded_source_and_reports_it(server):
    roster = {"Test": _cfg("Test", max_consecutive_shifts=4)}
    server._state["trailing_source"] = {
        "result": _december([("Test", d, DAY) for d in (28, 29, 30, 31)]),
        "source": "xlsx", "file": "/x/dec.xlsx",
    }
    trailing, prior = server._resolve_trailing_assignments(YEAR, MONTH, roster)
    assert [d.day for d, _ in trailing["Test"]] == [28, 29, 30, 31]
    assert prior["Test"].shifts_worked == 4 and prior["Test"].shifts_requested is None
    assert "2026-12" in server._state["trailing_last_used"]
    assert server.get_trailing_schedule().last_used == server._state["trailing_last_used"]


def test_resolver_ignores_a_loaded_source_for_the_wrong_month(server):
    server._state["trailing_source"] = {
        "result": _december([("Test", 31, DAY)]), "source": "xlsx", "file": "/x/dec.xlsx",
    }
    # Solving March: December is not its predecessor -> None, with a reason.
    out = server._resolve_trailing_assignments(2027, 3, {"Test": _cfg("Test")})
    assert out == (None, None)
    assert "2027-02" in server._state["trailing_last_used"]


def test_resolver_one_shot_file_overrides_loaded_source(server, tmp_path):
    roster = {"Test": _cfg("Test", max_consecutive_shifts=4)}
    server._state["trailing_source"] = {
        "result": _december([("Test", 31, DAY)]), "source": "xlsx", "file": "/x/old.xlsx",
    }
    dec = _december([("Test", d, DAY) for d in (30, 31)])
    path = tmp_path / "dec.xlsx"
    server._build_export_workbook(dec).save(path)

    trailing, _prior = server._resolve_trailing_assignments(YEAR, MONTH, roster, trailing_file=str(path))
    assert [d.day for d, _ in trailing["Test"]] == [30, 31]


def test_resolver_one_shot_file_that_is_unreadable_degrades_to_none(server, tmp_path):
    out = server._resolve_trailing_assignments(YEAR, MONTH, {}, trailing_file=str(tmp_path / "missing.xlsx"))
    assert out == (None, None)
    assert "could not read" in server._state["trailing_last_used"]


def test_load_trailing_with_preferences_directory_feeds_overage_carryover(server, tmp_path, monkeypatch):
    from scheduler.api.schemas import LoadTrailingScheduleRequest

    roster = {"Test": _cfg("Test")}
    server._state["roster"] = roster
    dec = _december([("Test", d, DAY) for d in range(20, 31)])          # 11 worked
    path = tmp_path / "dec.xlsx"
    server._build_export_workbook(dec).save(path)
    prefs = tmp_path / "prefs"
    prefs.mkdir()

    # Stand in for the real xlsx submission importer: one December
    # submission requesting 8 (so 11 worked == 3 over).
    def fake_import_directory(directory, year, month, glob_pattern="*.xlsx"):
        assert (year, month) == (DEC_YEAR, 12)
        return [PhysicianSubmission(physician_id="Test", physician_name="Test", year=year, month=month,
                                    shifts_requested=8, shifts_min=0, shifts_max=10, days=[])]
    monkeypatch.setattr(server, "import_directory", fake_import_directory)

    status = server.load_trailing_schedule(
        LoadTrailingScheduleRequest(file=str(path), preferences_directory=str(prefs)))
    assert status.requested_known_count == 1
    assert status.overage_physicians == ["Test"]
    assert status.acute_debt_physicians == []      # all 11 were RAH A side: no debt

    _trailing, prior = server._resolve_trailing_assignments(YEAR, MONTH, roster)
    assert prior["Test"].overage == 3


# --------------------------------------------------------------------------- #
# 4. Master-sheet link sources (credentialed / public export) and sked requests
# --------------------------------------------------------------------------- #

def _workbook_as_sheets_grid(wb) -> list[list]:
    """Rows as the Sheets API returns them: strings, blanks as "", ragged rows."""
    grid = []
    for row in wb.active.iter_rows(values_only=True):
        cells = ["" if v is None else str(v) for v in row]
        while cells and cells[-1] == "":
            cells.pop()
        grid.append(cells)
    return grid


def test_spreadsheet_id_from_url_accepts_links_and_bare_ids():
    from scheduler.backend import sheets_schedule_reader as r

    sid = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd"
    assert r.spreadsheet_id_from_url(f"https://docs.google.com/spreadsheets/d/{sid}/edit#gid=0") == sid
    assert r.spreadsheet_id_from_url(f"https://docs.google.com/spreadsheets/d/{sid}/view?usp=sharing") == sid
    assert r.spreadsheet_id_from_url(sid) == sid
    with pytest.raises(r.ScheduleSheetParseError):
        r.spreadsheet_id_from_url("https://example.com/not-a-sheet")
    with pytest.raises(r.ScheduleSheetParseError):
        r.spreadsheet_id_from_url("")


def test_parse_schedule_grid_reads_master_sheet_layout_from_string_cells(server):
    from scheduler.backend import sheets_schedule_reader as r

    roster = {"Test": _cfg("Test"), "Other": _cfg("Other")}
    dec = _december([("Test", 30, NIGHT), ("Test", 31, NIGHT), ("Other", 31, DAY)],
                    on_calls=[("Other", 31, "DOC")])
    grid = _workbook_as_sheets_grid(server._build_export_workbook(dec))

    parsed = r.parse_schedule_grid(grid, DEC_YEAR, 12, roster)

    got = sorted((a.physician_id, a.date.day, a.shift.code) for a in parsed.assignments)
    assert got == [("Other", 31, DAY.code), ("Test", 30, NIGHT.code), ("Test", 31, NIGHT.code)]
    assert [(o.physician_id, o.call_type) for o in parsed.on_calls] == [("Other", "DOC")]
    assert (parsed.year, parsed.month) == (DEC_YEAR, 12)


def test_download_public_sheet_grid_parses_an_export_and_rejects_a_sign_in_page(server, monkeypatch):
    import io
    import httpx
    from scheduler.backend import sheets_schedule_reader as r

    dec = _december([("Test", 31, DAY)])
    buf = io.BytesIO()
    server._build_export_workbook(dec).save(buf)
    xlsx_bytes = buf.getvalue()
    seen = {}

    class FakeResp:
        def __init__(self, status, ctype, content):
            self.status_code, self.headers, self.content = status, {"content-type": ctype}, content

    def fake_get(url, **kw):
        seen["url"] = url
        return FakeResp(200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", xlsx_bytes)
    monkeypatch.setattr(httpx, "get", fake_get)
    grid = r.download_public_sheet_grid("SHEETID_12345678901234567890")
    assert seen["url"].endswith("/export?format=xlsx")
    parsed = r.parse_schedule_grid(grid, DEC_YEAR, 12, {"Test": _cfg("Test")})
    assert [(a.physician_id, a.date.day) for a in parsed.assignments] == [("Test", 31)]

    monkeypatch.setattr(httpx, "get", lambda url, **kw: FakeResp(200, "text/html; charset=utf-8", b"<html>sign in</html>"))
    with pytest.raises(r.ScheduleSheetParseError, match="not publicly readable"):
        r.download_public_sheet_grid("SHEETID_12345678901234567890")


def test_load_trailing_route_from_sheet_link_without_credentials_uses_public_export(server, monkeypatch):
    from scheduler.api.schemas import LoadTrailingScheduleRequest
    from scheduler.backend import sheets_schedule_reader as r

    roster = {"Test": _cfg("Test")}
    server._state["roster"] = roster
    dec = _december([("Test", d, DAY) for d in (29, 30, 31)])
    grid = _workbook_as_sheets_grid(server._build_export_workbook(dec))
    sid = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd"
    monkeypatch.setattr(r, "download_public_sheet_grid", lambda spreadsheet_id, timeout=30.0: grid if spreadsheet_id == sid else [])

    status = server.load_trailing_schedule(LoadTrailingScheduleRequest(
        sheet_url=f"https://docs.google.com/spreadsheets/d/{sid}/edit", year=DEC_YEAR, month=12))

    assert status.loaded and status.source == "google_sheets_public_link"
    assert (status.year, status.month) == (DEC_YEAR, 12) and status.assignment_count == 3
    trailing, _prior = server._resolve_trailing_assignments(YEAR, MONTH, roster)
    assert [d.day for d, _ in trailing["Test"]] == [29, 30, 31]


def test_load_trailing_route_validates_source_combinations(server, tmp_path):
    from fastapi import HTTPException
    from scheduler.api.schemas import LoadTrailingScheduleRequest

    with pytest.raises(HTTPException, match="exactly one"):
        server.load_trailing_schedule(LoadTrailingScheduleRequest())
    with pytest.raises(HTTPException, match="exactly one"):
        server.load_trailing_schedule(LoadTrailingScheduleRequest(file="/x.xlsx", sheet_url="https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd/edit"))
    with pytest.raises(HTTPException, match="'year' and 'month'"):
        server.load_trailing_schedule(LoadTrailingScheduleRequest(sheet_url="https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd/edit"))
    with pytest.raises(HTTPException, match="at most one"):
        server.load_trailing_schedule(LoadTrailingScheduleRequest(file="/x.xlsx", preferences_directory="/p", sked_period_id="2026-12"))
    with pytest.raises(HTTPException, match="Not a Google Sheets link"):
        server.load_trailing_schedule(LoadTrailingScheduleRequest(sheet_url="https://example.com/x", year=DEC_YEAR, month=12))


def _fake_sked(monkeypatch, server, requested: dict[str, int], expect_period: str):
    """sked configured, and the period fetch returns one submission per entry in `requested`."""
    monkeypatch.setattr(server.sked_client, "load_sked_config", lambda: object())
    monkeypatch.setattr(server.sked_client, "is_fully_configured", lambda c: True)

    def fake_fetch(sked_config, period_id, year, month, roster):
        assert period_id == expect_period
        subs = [PhysicianSubmission(physician_id=pid, physician_name=pid, year=year, month=month,
                                    shifts_requested=n, shifts_min=0, shifts_max=n + 2, days=[])
                for pid, n in requested.items()]
        return subs, [], []
    monkeypatch.setattr(server, "_fetch_sked_period_submissions", fake_fetch)


def test_load_trailing_route_pulls_requested_counts_from_a_sked_period(server, tmp_path, monkeypatch):
    from scheduler.api.schemas import LoadTrailingScheduleRequest

    roster = {"Test": _cfg("Test")}
    server._state["roster"] = roster
    dec = _december([("Test", d, DAY) for d in range(20, 31)])   # 11 worked
    path = tmp_path / "dec.xlsx"
    server._build_export_workbook(dec).save(path)
    _fake_sked(monkeypatch, server, {"Test": 8}, expect_period="2026-12")

    status = server.load_trailing_schedule(LoadTrailingScheduleRequest(file=str(path), sked_period_id="2026-12"))

    assert status.requests_source == "sked" and status.requested_known_count == 1
    assert status.overage_physicians == ["Test"]
    _trailing, prior = server._resolve_trailing_assignments(YEAR, MONTH, roster)
    assert prior["Test"].overage == 3


def test_resolver_fallback_reads_master_sheet_and_sked_when_both_configured(server, monkeypatch):
    """Nothing loaded by hand: with google_sheets.yaml and sked.yaml both set up,
    the generate-time fallback finds December's master sheet by folder and
    December's requested counts from sked period 2026-12, automatically."""
    roster = {"Test": _cfg("Test", max_consecutive_shifts=4)}
    dec = _december([("Test", d, DAY) for d in range(22, 32)])   # 10 worked, all acute
    monkeypatch.setattr(server.google_sheets_client, "load_google_sheets_config", lambda: object())
    monkeypatch.setattr(server.google_sheets_client, "is_fully_configured", lambda c: True)

    def fake_parse(config, year, month, roster_):
        assert (year, month) == (DEC_YEAR, 12)
        return dec
    monkeypatch.setattr(server.sheets_schedule_reader, "parse_schedule_from_sheet", fake_parse)
    _fake_sked(monkeypatch, server, {"Test": 8}, expect_period="2026-12")

    trailing, prior = server._resolve_trailing_assignments(YEAR, MONTH, roster)

    assert [d.day for d, _ in trailing["Test"]] == [28, 29, 30, 31]
    assert prior["Test"].shifts_requested == 8 and prior["Test"].overage == 2
    assert "google_sheets + sked 2026-12" in server._state["trailing_last_used"]


def test_resolver_fallback_keeps_sheet_data_when_sked_fails(server, monkeypatch):
    roster = {"Test": _cfg("Test")}
    dec = _december([("Test", 31, DAY)])
    monkeypatch.setattr(server.google_sheets_client, "load_google_sheets_config", lambda: object())
    monkeypatch.setattr(server.google_sheets_client, "is_fully_configured", lambda c: True)
    monkeypatch.setattr(server.sheets_schedule_reader, "parse_schedule_from_sheet", lambda c, y, m, r: dec)
    monkeypatch.setattr(server.sked_client, "load_sked_config", lambda: object())
    monkeypatch.setattr(server.sked_client, "is_fully_configured", lambda c: True)

    def boom(sked_config, period_id, year, month, roster_):
        raise server.sked_client.SkedApiError("sked down")
    monkeypatch.setattr(server, "_fetch_sked_period_submissions", boom)

    trailing, prior = server._resolve_trailing_assignments(YEAR, MONTH, roster)
    assert [d.day for d, _ in trailing["Test"]] == [31]
    assert prior["Test"].shifts_requested is None          # overage term simply stays inert
