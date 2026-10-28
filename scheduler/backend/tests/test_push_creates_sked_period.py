"""Push to Master Sheet creates the month's sked period when it is missing, never overwrites an existing one."""

from __future__ import annotations

from scheduler.api import server


class _Cfg:
    base_url = "https://sked.example.org"


def _stub(monkeypatch, existing_ids):
    calls = {"upsert": [], "push": []}
    monkeypatch.setattr(server.sked_client, "list_periods",
                        lambda cfg, kind=None: [{"id": i, "label": i, "opens_at": "", "closes_at": "", "kind": "shift_request"} for i in existing_ids])
    monkeypatch.setattr(server.sked_client, "upsert_period",
                        lambda cfg, pid, label, opens, closes: calls["upsert"].append((pid, label, opens, closes)))
    return calls


def test_missing_period_is_created_with_whole_month_window(monkeypatch):
    calls = _stub(monkeypatch, ["2027-02"])
    assert server._ensure_sked_period(_Cfg(), 2027, 1) is True
    assert calls["upsert"] == [("2027-01", "January 2027", "2027-01-01", "2027-01-31")]


def test_existing_period_is_left_alone(monkeypatch):
    calls = _stub(monkeypatch, ["2027-01"])
    assert server._ensure_sked_period(_Cfg(), 2027, 1) is False
    assert calls["upsert"] == []
