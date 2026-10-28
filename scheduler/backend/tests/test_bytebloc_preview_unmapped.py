"""GET /api/bytebloc/preview surfaces valid physicians missing from provider_map as a separate list."""

from __future__ import annotations

import datetime

from fastapi.testclient import TestClient

from scheduler.api import server
from scheduler.backend.config import PhysicianConfig
from scheduler.backend.models import DayAvailability, PhysicianSubmission


class _Cfg:
    group_code = "G"; location_code = "L"; requester_id = "R"
    provider_map = {"Mapped": "123"}


def _sub(pid):
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=2027, month=2, shifts_requested=1,
                               shifts_min=0, shifts_max=1,
                               days=[DayAvailability(date=datetime.date(2027, 2, 1), wants_to_work=True,
                                                     available_blocks=frozenset(), requested_shifts=frozenset())])


def test_unmapped_valid_physicians_are_listed(monkeypatch):
    roster = {"Mapped": PhysicianConfig(id="Mapped", name="Mapped", first_name="M", last_name="Apped"),
              "Lost": PhysicianConfig(id="Lost", name="Lost", first_name="L", last_name="Ost")}
    monkeypatch.setitem(server._state, "roster", roster)
    monkeypatch.setitem(server._state, "year", 2027)
    monkeypatch.setitem(server._state, "month", 2)
    monkeypatch.setattr(server, "_build_bytebloc_payload",
                        lambda use_delta=True: (_Cfg(), {"SkedStartDate": "2027-02-01"}, ["w"], [], False, 0))
    monkeypatch.setattr(server, "_eligible_submissions_for_bytebloc", lambda: ([_sub("Mapped"), _sub("Lost")], 0))
    res = TestClient(server.app).get("/api/bytebloc/preview")
    assert res.status_code == 200, res.text
    assert res.json()["unmapped_physicians"] == ["Ost, L"]
