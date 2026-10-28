"""Learner extraction from a master-sheet grid (text + fill colour of the row under each shift)."""

from __future__ import annotations

import datetime

from scheduler.backend import sheets_schedule_reader as r
from scheduler.backend.config import PhysicianConfig
from scheduler.backend.shifts import EXPORT_SHIFTS

YEAR, MONTH = 2026, 12   # Dec 2026: week 1 is Sun Nov 29 .. Sat Dec 5


def _sheet():
    values = [["", "SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"], ["", "", "", "", "2", "3", "4", "5"]]
    fills = [[""] * 8, [""] * 8]
    for site_label, time_label, tc, _ in EXPORT_SHIFTS:
        row_fill = "ffcc00" if tc is None else "cfe2f3"
        values.append([site_label] + [""] * 7 + [site_label]); fills.append([row_fill] * 9)
        values.append([time_label] + [""] * 7 + [time_label]); fills.append([row_fill if tc is None else ""] * 9)
    # legend in column C (Monday, a blank day) rows 6..9 (1-based) -> index 5..8
    legend = [("LEGEND", "ffffff"), ("EM resident", "4a86e8"), ("Off service resident", "ff0000"), ("Core Student", "00ff00")]
    for i, (t, f) in enumerate(legend):
        values[5 + i][2] = t; fills[5 + i][2] = "" if f == "ffffff" else f
    return values, fills


def _set(values, fills, label, time_label, col, name, fill, learner, lfill):
    for i in range(len(values) - 1):
        if values[i][0] == label and values[i + 1][0] == time_label:
            values[i][col] = name; fills[i][col] = fill
            values[i + 1][col] = learner; fills[i + 1][col] = lfill
            return
    raise AssertionError(label)


def test_learners_resolved_from_the_sheets_own_legend_and_split_on_slash():
    values, fills = _sheet()
    _set(values, fills, "RAH A", "0600-1200", 4, "Smith", "cfe2f3", "Wengryn", "4a86e8")       # Wed Dec 2
    _set(values, fills, "RAH B", "0600-1200", 5, "Jones", "cfe2f3", "Lee/Park", "00ff00")      # Thu Dec 3
    _set(values, fills, "NECHC", "0600-1400", 5, "Chan", "cfe2f3", "No learner", "")
    _set(values, fills, "DOC", "0500-1559", 4, "Call", "ffcc00", "No learner", "ffcc00")
    _set(values, fills, "RAH I", "0600-1400", 6, "Deol", "cfe2f3", "Mystery", "123456")         # colour not in legend
    out = r.extract_learners(values, fills, YEAR, MONTH)
    assert out[("shift", datetime.date(2026, 12, 2), "0600h RAH A side")] == [{"name": "Wengryn", "type": "EM resident", "color": "#4a86e8"}]
    assert out[("shift", datetime.date(2026, 12, 3), "0600h RAH B side")] == [
        {"name": "Lee", "type": "Core Student", "color": "#00ff00"}, {"name": "Park", "type": "Core Student", "color": "#00ff00"}]
    assert ("shift", datetime.date(2026, 12, 3), "0600h NEHC") not in out
    assert ("call", datetime.date(2026, 12, 2), "DOC") not in out
    assert out[("shift", datetime.date(2026, 12, 4), "0600h RAH I side")] == [{"name": "Mystery", "type": "", "color": "#123456"}]


def test_call_row_learner_keeps_name_but_amber_is_not_a_type():
    values, fills = _sheet()
    _set(values, fills, "NOC", "1600-0459", 4, "Call", "ffcc00", "Resident X", "ffcc00")
    out = r.extract_learners(values, fills, YEAR, MONTH)
    assert out[("call", datetime.date(2026, 12, 2), "NOC")] == [{"name": "Resident X", "type": "", "color": ""}]


def test_default_legend_used_when_sheet_has_none():
    values, fills = _sheet()
    for i in range(5, 9):
        values[i][2] = ""; fills[i][2] = ""
    _set(values, fills, "RAH A", "0600-1200", 4, "Smith", "cfe2f3", "Someone", "9900ff")
    out = r.extract_learners(values, fills, YEAR, MONTH)
    assert out[("shift", datetime.date(2026, 12, 2), "0600h RAH A side")][0]["type"] == "Elective resident"


def test_tolerant_parse_collects_unresolved_names_instead_of_raising():
    values, fills = _sheet()
    _set(values, fills, "RAH A", "0600-1200", 4, "Smith", "cfe2f3", "", "")
    _set(values, fills, "RAH B", "0600-1200", 4, "Nobody Known", "cfe2f3", "", "")
    roster = {"Smith": PhysicianConfig(id="Smith", name="Smith", last_name="Smith")}
    unresolved: list[str] = []
    res = r.parse_schedule_grid(values, YEAR, MONTH, roster, unresolved=unresolved)
    assert [a.physician_id for a in res.assignments] == ["Smith"]
    assert unresolved == ["Nobody Known"]
