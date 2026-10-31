"""
The Send Monthly Shift Requests pane no longer needs a hand-made xlsx:
/api/monthly-requests/plan describes the month, /template-preview builds the
template for inspection, and /send with generate_template builds and uploads
it to sked in memory (user decision 2026-10-31).
"""

from __future__ import annotations

import io

import openpyxl
from fastapi.testclient import TestClient

from scheduler.api import server
from scheduler.backend import email_sender, sked_client
from scheduler.backend.config import PhysicianConfig


class _Cfg:
    base_url = "https://sked.test"
    admin_secret = "x"
    link_days_valid = 31
    survey_base_url = ""


def test_plan_describes_the_period_and_pre_marks_holidays():
    client = TestClient(server.app)
    r = client.post("/api/monthly-requests/plan", json={"year": 2026, "month": 12})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["period_id"] == "2026-12" and plan["label"] == "December 2026"
    assert plan["days"][0]["date"] == "2026-12-02" and plan["days"][-1]["date"] == "2027-01-01"
    assert plan["days"][-1]["in_month"] is False and plan["spillover_day"] == "2027-01-01"
    assert [m["date"] for m in plan["suggested_marks"]][0] == "2026-12-24"
    assert {m["label"] for m in plan["suggested_marks"]} == {"Christmas Rules Apply"}
    assert plan["optional_holidays"] == [{"date": "2026-12-26", "label": "Boxing Day"}]
    assert plan["default_opens_at"] == "2026-11-01" and plan["default_closes_at"] == "2026-11-30"
    assert plan["master_ok"] is True
    assert plan["template_filename"].startswith("December 2026 Master Preferences")

    r = client.post("/api/monthly-requests/plan", json={"year": 2027, "month": 3})
    plan = r.json()
    assert plan["days"][0]["date"] == "2027-03-01" and len(plan["days"]) == 31
    assert plan["suggested_marks"] == [{"date": "2027-03-26", "label": "Good Friday"}]
    assert plan["optional_holidays"] == [{"date": "2027-03-29", "label": "Easter Monday"}]


def test_preview_writes_the_template_under_the_config_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_CONFIG_DIR", tmp_path)
    client = TestClient(server.app)
    r = client.post("/api/monthly-requests/template-preview", json={
        "year": 2027, "month": 3, "closes_at": "2027-01-31",
        "marks": [{"date": "2027-03-26", "label": "Good Friday"},
                  {"date": "2027-03-27", "label": "EPSB Spring Break"},
                  {"date": "2027-03-28", "label": "EPSB Spring Break"}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"].startswith(str(tmp_path / "generated-templates"))
    assert body["marks_summary"] == ["Mar 26: Good Friday", "Mar 27 - Mar 28: EPSB Spring Break"]
    ws = openpyxl.load_workbook(body["path"]).worksheets[0]
    assert ws["A60"].value == "Return by MIDNIGHT January 31 to keasked@gmail.com"
    assert ws.cell(2, 27).value == "Good Friday"

    bad = client.post("/api/monthly-requests/template-preview", json={
        "year": 2027, "month": 1, "marks": [{"date": "2027-01-01", "label": "x"}],
    })
    assert bad.status_code == 400 and "outside the period" in bad.json()["detail"]


def test_send_generates_and_uploads_the_template(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sked_client, "load_sked_config", lambda: _Cfg())
    monkeypatch.setattr(sked_client, "is_fully_configured", lambda cfg: True)
    monkeypatch.setattr(email_sender, "load_email_config", lambda: object())
    monkeypatch.setattr(email_sender, "is_fully_configured", lambda cfg: True)
    server._state["roster"] = {
        "Alpha": PhysicianConfig(id="Alpha", name="Alpha", active=True, email="a@example.test"),
    }

    calls: dict = {}
    monkeypatch.setattr(sked_client, "upsert_period", lambda cfg, pid, label, o, c: calls.setdefault("period", (pid, label)))
    monkeypatch.setattr(sked_client, "upload_period_template",
                        lambda *a, **k: calls.setdefault("file_upload", True))

    def _upload_bytes(cfg, period_id, filename, data):
        calls["upload"] = (period_id, filename, data)
    monkeypatch.setattr(sked_client, "upload_period_template_bytes", _upload_bytes)
    monkeypatch.setattr(sked_client, "generate_period_links", lambda cfg, pid, phys, base_url_override=None: [
        {"id": p["id"], "name": p["name"], "email": p["email"], "url": f"https://sked.test/?t={p['id']}", "hasEmail": bool(p["email"])}
        for p in phys
    ])
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(email_sender, "send_email", lambda to, subject, body, cfg: sent.append((to, subject, body)))

    client = TestClient(server.app)
    r = client.post("/api/monthly-requests/send", json={
        "period_id": "2027-03", "label": "March 2027",
        "opens_at": "2027-02-01T00:00:00Z", "closes_at": "2027-02-28T00:00:00Z",
        "generate_template": True, "year": 2027, "month": 3,
        "marks": [{"date": "2027-03-26", "label": "Good Friday"},
                  {"date": "2027-03-27", "label": "EPSB Spring Break"}],
    })
    assert r.status_code == 200, r.text
    assert r.json()["sent_count"] == 1
    assert "file_upload" not in calls
    period_id, filename, data = calls["upload"]
    assert period_id == "2027-03" and filename == "March 2027 Master Preferences with 0600 and night Q.xlsx"
    ws = openpyxl.load_workbook(io.BytesIO(data)).worksheets[0]
    assert ws["B3"].value == 1 and ws["AF3"].value == 31 and ws["AG3"].value is None
    assert ws["A60"].value == "Return by MIDNIGHT February 28 to keasked@gmail.com"
    assert (tmp_path / "generated-templates" / filename).is_file()   # a copy of what went out
    body = sent[0][2]
    assert "The sheet covers Mar 1 to Mar 31." in body
    assert "Please submit by midnight on February 28." in body
    assert "Mar 26: Good Friday; Mar 27: EPSB Spring Break" in body

    # No template source at all: the period is reused and nothing is uploaded.
    calls.clear()
    r = client.post("/api/monthly-requests/send", json={
        "period_id": "2027-03", "label": "March 2027",
        "opens_at": "2027-02-01T00:00:00Z", "closes_at": "2027-02-28T00:00:00Z",
    })
    assert r.status_code == 200 and "upload" not in calls and "file_upload" not in calls

    # generate_template without a month is a client error, not a crash.
    r = client.post("/api/monthly-requests/send", json={
        "period_id": "2027-03", "label": "March 2027",
        "opens_at": "2027-02-01T00:00:00Z", "closes_at": "2027-02-28T00:00:00Z",
        "generate_template": True,
    })
    assert r.status_code == 400
