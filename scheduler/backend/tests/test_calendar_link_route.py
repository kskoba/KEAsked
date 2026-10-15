"""POST /api/sked/calendar-link: one physician's permanent .ics feed + live-view URLs (Roster Editor button)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from scheduler.api import server
from scheduler.backend.config import PhysicianConfig


class _Cfg:
    base_url = "https://sked.example.org"


def _stub(monkeypatch, feed: str):
    monkeypatch.setattr(server.sked_client, "load_sked_config", lambda: _Cfg())
    monkeypatch.setattr(server.sked_client, "is_fully_configured", lambda c: True)
    calls = []
    def mint(cfg, pid):
        calls.append(pid); return feed
    monkeypatch.setattr(server.sked_client, "get_calendar_link", mint)
    monkeypatch.setitem(server._state, "roster", {"KS": PhysicianConfig(id="KS", name="K S")})
    return calls


def test_returns_feed_and_matching_live_view_url(monkeypatch):
    feed = "https://sked.example.org/api/calendar/S1M.X19jYWxlbmRhcl9f.4070908800.sig.ics"
    calls = _stub(monkeypatch, feed)
    res = TestClient(server.app).post("/api/sked/calendar-link", json={"physician_id": "KS"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["physician_id"] == "KS" and body["feed_url"] == feed
    assert body["live_url"] == "https://sked.example.org/my-schedule?token=S1M.X19jYWxlbmRhcl9f.4070908800.sig"
    assert calls == ["KS"]


def test_unknown_physician_is_404(monkeypatch):
    _stub(monkeypatch, "https://sked.example.org/api/calendar/x.ics")
    res = TestClient(server.app).post("/api/sked/calendar-link", json={"physician_id": "Nobody"})
    assert res.status_code == 404


def test_unconfigured_sked_is_400(monkeypatch):
    monkeypatch.setattr(server.sked_client, "load_sked_config", lambda: None)
    res = TestClient(server.app).post("/api/sked/calendar-link", json={"physician_id": "KS"})
    assert res.status_code == 400
