"""
Cross-month memory of anchor (2400h / 0600h) overage -- item 5 of the
2026-10-29 jan6 feedback round (feature `anchor_ledger`).

When a month is finalised, every physician who worked MORE 2400h or 0600h
shifts than their submission stated is written to a small ledger file in
the config directory. For the next few months the solver reads the ledger
and charges extra for an unrequested anchor of that type on anyone listed
(and withdraws the "night person" discount), so the same people are not
the ones absorbing overage month after month.

The ledger is written from three places (server.py):
  * the Push to Master Sheet route, once the sheet write succeeded;
  * the trailing-month resolver, whenever the previous month's finalized
    schedule AND its submissions are both in hand (the steady state: the
    sheet plus sked's period) -- so the ledger fills itself even when the
    push never ran from this machine;
  * POST /api/anchor-ledger/record, for recording the active schedule by
    hand.
Re-recording a month replaces that month's entries, never duplicates them.

File: <config dir>/anchor_overage_ledger.yaml (gitignored; real names).
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Iterable

import yaml

from scheduler.backend.config import _resolve_config_dir
from scheduler.backend.generator import ScheduleResult
from scheduler.backend.models import PhysicianSubmission

LEDGER_FILENAME = "anchor_overage_ledger.yaml"
# How many months an entry stays active ("not again for a few months").
DEFAULT_MONTHS_BACK = 3
ANCHOR_TYPES = ("2400h", "0600h")


def ledger_path(path: str | Path | None = None) -> Path:
    return Path(path) if path else _resolve_config_dir() / LEDGER_FILENAME


def load(path: str | Path | None = None) -> list[dict]:
    fp = ledger_path(path)
    if not fp.is_file():
        return []
    data = yaml.safe_load(fp.read_text(encoding="utf-8")) or {}
    entries = data.get("entries") or []
    return [dict(e) for e in entries if isinstance(e, dict)]


def _save(entries: list[dict], path: str | Path | None = None) -> None:
    fp = ledger_path(path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(entries, key=lambda e: (int(e["year"]), int(e["month"]), str(e["physician_id"]), str(e["type"])))
    fp.write_text(
        "# Anchor-overage ledger -- written by the scheduler, see scheduler/backend/anchor_ledger.py.\n"
        "# One entry per physician / month / anchor type worked beyond the stated request.\n"
        + yaml.safe_dump({"entries": ordered}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def month_overage(result: ScheduleResult, submissions: Iterable[PhysicianSubmission]) -> list[dict]:
    """Entries for this result's month: physicians whose 2400h / 0600h count
    exceeded the count their submission STATED (a blank cell is no request,
    so it never counts as overage here -- the fair-share rules cover it)."""
    worked: dict[tuple[str, str], int] = {}
    for a in list(result.assignments) + list(result.spillover):
        if a.shift is not None and a.shift.time in ANCHOR_TYPES:
            key = (a.physician_id, a.shift.time)
            worked[key] = worked.get(key, 0) + 1
    out: list[dict] = []
    for sub in submissions:
        if (sub.year, sub.month) != (result.year, result.month):
            continue
        for time_str, stated, requested in (
            ("2400h", sub.shifts_2400h_stated, sub.shifts_2400h_requested),
            ("0600h", sub.shifts_0600h_stated, sub.shifts_0600h_requested),
        ):
            if not stated:
                continue
            got = worked.get((sub.physician_id, time_str), 0)
            if got > requested:
                out.append({
                    "physician_id": sub.physician_id, "year": result.year, "month": result.month,
                    "type": time_str, "requested": int(requested), "worked": int(got), "over": int(got - requested),
                })
    return out


def record_month(result: ScheduleResult, submissions: Iterable[PhysicianSubmission],
                 path: str | Path | None = None) -> list[dict]:
    """Replace the ledger's entries for result's month with what this
    schedule + these submissions show. Returns the entries written."""
    fresh = month_overage(result, submissions)
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    for e in fresh:
        e["recorded_at"] = stamp
    kept = [e for e in load(path) if (int(e.get("year", 0)), int(e.get("month", 0))) != (result.year, result.month)]
    _save(kept + fresh, path)
    return fresh


def _months_before(year: int, month: int, n: int) -> set[tuple[int, int]]:
    out = set()
    y, m = year, month
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.add((y, m))
    return out


def recent_overage(year: int, month: int, months_back: int = DEFAULT_MONTHS_BACK,
                   path: str | Path | None = None) -> dict[str, set[str]]:
    """physician_id -> anchor types they went over on in any of the
    `months_back` months before (year, month). This is what the solver
    receives for a solve of (year, month)."""
    window = _months_before(year, month, months_back)
    out: dict[str, set[str]] = {}
    for e in load(path):
        try:
            key = (int(e["year"]), int(e["month"]))
        except (KeyError, TypeError, ValueError):
            continue
        if key in window and e.get("type") in ANCHOR_TYPES and int(e.get("over", 0)) > 0:
            out.setdefault(str(e["physician_id"]), set()).add(str(e["type"]))
    return out


def describe(active: dict[str, set[str]]) -> str:
    if not active:
        return "anchor ledger: nobody carried over"
    parts = [f"{pid} ({'/'.join(sorted(types))})" for pid, types in sorted(active.items())]
    return f"anchor ledger: {len(active)} physician(s) charged extra for unrequested anchors this month: " + ", ".join(parts)
