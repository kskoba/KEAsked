"""POST /api/sked/calendar-links/email: mail physicians their permanent .ics feed + live-view links (Roster Editor buttons)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from scheduler.api import server
from scheduler.backend.config import PhysicianConfig


class _Sked:
    base_url = "https://sked.example.org"


class _Mail:
    pass


def _stub(monkeypatch, roster, fail_link_for=(), fail_send_for=()):
    monkeypatch.setattr(server.sked_client, "load_sked_config", lambda: _Sked())
    monkeypatch.setattr(server.sked_client, "is_fully_configured", lambda c: True)
    monkeypatch.setattr(server.email_sender, "load_email_config", lambda: _Mail())
    monkeypatch.setattr(server.email_sender, "is_fully_configured", lambda c: True)
    monkeypatch.setattr(server, "load_roster", lambda: roster)

    def mint(cfg, pid):
        if pid in fail_link_for:
            raise server.sked_client.SkedApiError(f"sked down for {pid}")
        return f"https://sked.example.org/api/calendar/TOKEN-{pid}.ics"
    monkeypatch.setattr(server.sked_client, "get_calendar_link", mint)

    sent = []
    def send(to, subject, body, config):
        if to in fail_send_for:
            raise RuntimeError("SMTP refused")
        sent.append((to, subject, body))
    monkeypatch.setattr(server.email_sender, "send_email", send)
    return sent


ROSTER = {
    "A": PhysicianConfig(id="A", name="A", first_name="Ann", last_name="Able", email="a@x.org", active=True),
    "B": PhysicianConfig(id="B", name="B", first_name="Bo", last_name="Blank", email="", active=True),
    "C": PhysicianConfig(id="C", name="C", first_name="Cy", last_name="Gone", email="c@x.org", active=False),
}


def test_bulk_emails_active_with_email_and_lists_the_rest(monkeypatch):
    sent = _stub(monkeypatch, ROSTER)
    res = TestClient(server.app).post("/api/sked/calendar-links/email", json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["sent_count"] == 1 and body["ok"] is False
    assert [r["physician_id"] for r in body["needs_attention"]] == ["B"]
    assert body["needs_attention"][0]["status"] == "no_email"
    # inactive C is not a target at all
    assert {r["physician_id"] for r in body["results"]} == {"A", "B"}
    to, subject, text = sent[0]
    assert to == "a@x.org"
    assert "https://sked.example.org/api/calendar/TOKEN-A.ics" in text
    assert "https://sked.example.org/my-schedule?token=TOKEN-A" in text
    assert "Google Calendar" in text and "Apple Calendar" in text


def test_explicit_ids_include_inactive_and_append_extra_message(monkeypatch):
    sent = _stub(monkeypatch, ROSTER)
    res = TestClient(server.app).post(
        "/api/sked/calendar-links/email",
        json={"physician_ids": ["C"], "extra_message": "Welcome back!"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["sent_count"] == 1 and res.json()["ok"] is True
    assert sent[0][0] == "c@x.org" and sent[0][2].rstrip().endswith("Welcome back!")


def test_link_and_send_failures_are_reported_per_physician(monkeypatch):
    roster = dict(ROSTER, D=PhysicianConfig(id="D", name="D", email="d@x.org", active=True))
    _stub(monkeypatch, roster, fail_link_for={"A"}, fail_send_for={"d@x.org"})
    res = TestClient(server.app).post("/api/sked/calendar-links/email", json={"physician_ids": ["A", "D"]})
    assert res.status_code == 200, res.text
    statuses = {r["physician_id"]: r["status"] for r in res.json()["results"]}
    assert statuses == {"A": "link_failed", "D": "send_failed"}
    assert res.json()["sent_count"] == 0


def test_unknown_id_is_404(monkeypatch):
    sent = _stub(monkeypatch, ROSTER)
    res = TestClient(server.app).post("/api/sked/calendar-links/email", json={"physician_ids": ["Nobody"]})
    assert res.status_code == 404 and sent == []
