"""PhysicianImportResult.source_file: the Validate page's open-file button
needs the submission's path when it still exists, and nothing otherwise."""

from __future__ import annotations

import calendar
import datetime

from scheduler.api import server
from scheduler.backend.config import PhysicianConfig
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES


def _sub(pid: str, source_file: str) -> PhysicianSubmission:
    days = [DayAvailability(date=datetime.date(2027, 1, d), wants_to_work=True,
                            available_blocks=frozenset(range(5)), requested_shifts=frozenset(ALL_SHIFT_CODES))
            for d in range(1, calendar.monthrange(2027, 1)[1] + 1)]
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=2027, month=1,
                               shifts_requested=8, shifts_min=0, shifts_max=10, days=days, source_file=source_file)


def test_source_file_is_reported_only_when_the_file_exists(tmp_path):
    real = tmp_path / "Test - January 2027.xlsx"
    real.write_bytes(b"x")
    roster = {"Test": PhysicianConfig(id="Test", name="Test"), "Gone": PhysicianConfig(id="Gone", name="Gone")}
    results = server._build_import_results([_sub("Test", str(real)), _sub("Gone", str(tmp_path / "deleted.xlsx"))], set(), roster)
    by = {r.physician_id: r for r in results}
    assert by["Test"].source_file == str(real)
    assert by["Gone"].source_file is None          # e.g. a sked import's temp file, already removed
