"""
POST /api/sked/export-all: write every submitted physician's sked preference
xlsx into a user-chosen folder, one file each, named
"<RosterId> - <Month YYYY>.xlsx" (user decisions 2026-10-16: drafts skipped
unless include_drafts; no default folder; one failing fetch must not stop
the rest; progress is polled).
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from scheduler.api import server
from scheduler.backend import sked_client
from scheduler.backend.config import PhysicianConfig


class _Cfg:
    base_url = "https://sked.test"
    admin_secret = "x"


def _roster():
    return {
        "Alpha": PhysicianConfig(id="Alpha", name="Alpha", active=True),
        "Beta": PhysicianConfig(id="Beta", name="Beta", active=True),
        "Gamma": PhysicianConfig(id="Gamma", name="Gamma", active=True),
        "Delta": PhysicianConfig(id="Delta", name="Delta", active=True),
        "Old": PhysicianConfig(id="Old", name="Old", active=False),
    }


ROWS = [
    {"physicianId": "Alpha", "physicianName": "Alpha", "status": "submitted", "updatedAt": ""},
    {"physicianId": "Beta", "physicianName": "Beta", "status": "draft", "updatedAt": ""},
    {"physicianId": "Gamma", "physicianName": "Gamma", "status": "submitted", "updatedAt": ""},
    # Delta: no row at all -> not submitted
    {"physicianId": "Old", "physicianName": "Old", "status": "submitted", "updatedAt": ""},  # inactive -> ignored
]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(sked_client, "load_sked_config", lambda: _Cfg())
    monkeypatch.setattr(sked_client, "is_fully_configured", lambda cfg: True)
    monkeypatch.setattr(server, "load_roster", _roster)
    monkeypatch.setattr(sked_client, "list_period_submissions", lambda cfg, period_id: list(ROWS))

    def fake_export(cfg, pid, period_id):
        if pid == "Gamma":
            raise sked_client.SkedApiError("sked says no")
        return f"xlsx-bytes-for-{pid}-{period_id}".encode()

    monkeypatch.setattr(sked_client, "fetch_physician_export", fake_export)
    server._state.pop("sked_export", None)
    return TestClient(server.app)


def _wait(client) -> dict:
    for _ in range(100):
        p = client.get("/api/sked/export-all/progress").json()
        if p["finished"]:
            return p
        time.sleep(0.02)
    raise AssertionError("export did not finish")


def test_writes_submitted_only_with_the_folder_naming_convention(client, tmp_path):
    res = client.post("/api/sked/export-all", json={
        "period_id": "2027-01", "year": 2027, "month": 1, "directory": str(tmp_path),
    })
    assert res.status_code == 200, res.text
    p = _wait(client)
    assert p["skipped_draft"] == ["Beta"]
    assert p["skipped_not_submitted"] == ["Delta"]
    assert p["written"] == [str(tmp_path / "Alpha - January 2027.xlsx")]
    assert (tmp_path / "Alpha - January 2027.xlsx").read_bytes() == b"xlsx-bytes-for-Alpha-2027-01"
    assert p["errors"] == ["Gamma: sked says no"]      # one failure, rest still written
    assert p["total"] == 2 and p["current"] == 2 and not p["running"] and not p["cancelled"]
    assert not (tmp_path / "Old - January 2027.xlsx").exists()


def test_include_drafts_exports_the_draft_too(client, tmp_path):
    client.post("/api/sked/export-all", json={
        "period_id": "2027-01", "year": 2027, "month": 1, "directory": str(tmp_path), "include_drafts": True,
    })
    p = _wait(client)
    assert p["skipped_draft"] == []
    assert (tmp_path / "Beta - January 2027.xlsx").exists()


def test_existing_file_is_replaced(client, tmp_path):
    (tmp_path / "Alpha - January 2027.xlsx").write_bytes(b"stale")
    client.post("/api/sked/export-all", json={
        "period_id": "2027-01", "year": 2027, "month": 1, "directory": str(tmp_path),
    })
    _wait(client)
    assert (tmp_path / "Alpha - January 2027.xlsx").read_bytes() == b"xlsx-bytes-for-Alpha-2027-01"


def test_missing_folder_is_rejected_not_created(client, tmp_path):
    missing = tmp_path / "nope"
    res = client.post("/api/sked/export-all", json={
        "period_id": "2027-01", "year": 2027, "month": 1, "directory": str(missing),
    })
    assert res.status_code == 400
    assert not missing.exists()


def test_period_listing_failure_is_reported_as_a_fatal_error(client, tmp_path, monkeypatch):
    def boom(cfg, period_id):
        raise sked_client.SkedApiError("listing failed")
    monkeypatch.setattr(sked_client, "list_period_submissions", boom)
    client.post("/api/sked/export-all", json={
        "period_id": "2027-01", "year": 2027, "month": 1, "directory": str(tmp_path),
    })
    p = _wait(client)
    assert p["error"] == "listing failed" and p["written"] == []
