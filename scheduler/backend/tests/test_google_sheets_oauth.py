"""google_sheets.yaml `auth: oauth`: config parsing, readiness, and credential loading from a saved token."""

from __future__ import annotations

import json

import pytest

from scheduler.backend import google_sheets_client as g


def _cfg(tmp_path, monkeypatch, auth="oauth", token=True, secrets=True):
    monkeypatch.setattr(g, "_CONFIG_DIR", tmp_path)
    (tmp_path / "google_sheets.yaml").write_text(
        f'auth: "{auth}"\ndrive_folder_id: "FOLDER"\n', encoding="utf-8")
    if secrets:
        (tmp_path / "google_oauth_client.json").write_text("{}", encoding="utf-8")
    if token:
        (tmp_path / "google_oauth_token.json").write_text(json.dumps({
            "token": "ya29.x", "refresh_token": "1//r", "client_id": "cid", "client_secret": "cs",
            "token_uri": "https://oauth2.googleapis.com/token", "scopes": g._SCOPES,
            "expiry": "2099-01-01T00:00:00Z",
        }), encoding="utf-8")
    return g.load_google_sheets_config(tmp_path / "google_sheets.yaml")


def test_oauth_config_is_ready_only_once_signed_in(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, token=False)
    assert cfg.auth == "oauth"
    assert not g.is_fully_configured(cfg)
    assert "Not signed in" in g.status_detail(cfg)
    cfg = _cfg(tmp_path, monkeypatch, token=True)
    assert g.is_fully_configured(cfg)
    assert g.status_detail(cfg) == "Signed in (OAuth)."


def test_oauth_credentials_load_from_saved_token(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    creds = g._get_credentials(cfg)
    assert creds.token == "ya29.x" and creds.refresh_token == "1//r" and creds.valid


def test_missing_token_is_a_clear_error(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, token=False)
    with pytest.raises(g.GoogleSheetsApiError, match="google-oauth-login"):
        g._get_credentials(cfg)


def test_unknown_auth_value_is_rejected(tmp_path, monkeypatch):
    with pytest.raises(g.GoogleSheetsApiError):
        _cfg(tmp_path, monkeypatch, auth="magic")


def test_service_account_mode_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "_CONFIG_DIR", tmp_path)
    (tmp_path / "google_sheets.yaml").write_text(
        'drive_folder_id: "FOLDER"\nservice_account_key_path: "sa.json"\n', encoding="utf-8")
    cfg = g.load_google_sheets_config(tmp_path / "google_sheets.yaml")
    assert cfg.auth == "service_account" and not g.is_fully_configured(cfg)
    (tmp_path / "sa.json").write_text("{}", encoding="utf-8")
    assert g.is_fully_configured(cfg)
