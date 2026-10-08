"""POST /api/clear-month forgets the loaded month but keeps setup state."""

from __future__ import annotations

import pytest

from scheduler.backend.generator import ScheduleResult


@pytest.fixture
def server():
    from scheduler.api import server as server_mod
    keys = ("submissions", "result", "year", "month", "directory", "generator", "overrides", "progress", "trailing_source", "roster")
    saved = {k: server_mod._state[k] for k in keys}
    yield server_mod
    server_mod._state.update(saved)


def test_clear_month_resets_month_state_and_keeps_setup(server):
    server._state.update(submissions=["x"], result=ScheduleResult(year=2027, month=1), year=2027, month=1,
                         directory="/tmp/jan", generator=object(), overrides={"A": {"r"}},
                         trailing_source={"result": ScheduleResult(year=2026, month=12), "source": "xlsx", "file": "/d.xlsx"},
                         roster={"keep": "me"})
    assert server.clear_loaded_month() == {"cleared": True}
    assert server._state["submissions"] == [] and server._state["result"] is None
    assert server._state["year"] is None and server._state["month"] is None
    assert server._state["generator"] is None and server._state["overrides"] == {}
    assert server._state["trailing_source"]["source"] == "xlsx"       # previous month kept
    assert server._state["roster"] == {"keep": "me"}


def test_clear_month_refuses_while_a_solve_is_running(server):
    from fastapi import HTTPException
    server._state["progress"] = {"current": 50, "total": 100, "running": True}
    with pytest.raises(HTTPException) as e:
        server.clear_loaded_month()
    assert e.value.status_code == 409
