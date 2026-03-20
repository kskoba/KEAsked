"""
Monthly shift-request history tracker.

Persists each physician's requested/min/max shift counts per month so the
validator can flag unusual variances.

Storage: scheduler/data/shift_history.json
Schema:
  {
    "<physician_id>": {
      "YYYY-MM": {"requested": N, "min": M, "max": X},
      ...
    }
  }
"""

from __future__ import annotations

import json
from pathlib import Path

from scheduler.backend.models import PhysicianSubmission, ValidationIssue

_DEFAULT_HISTORY_PATH = (
    Path(__file__).parent.parent / "data" / "shift_history.json"
)

# A variance of more than this many shifts from recent history triggers a warning.
_VARIANCE_THRESHOLD = 2

# Number of most-recent months to average when checking variance.
_LOOKBACK_MONTHS = 6


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #

def _load_raw(path: Path) -> dict:
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def _save_raw(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


def _month_key(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


def _sorted_keys(physician_history: dict) -> list[str]:
    """Return month keys in chronological order."""
    return sorted(physician_history.keys())


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def record(
    submission: PhysicianSubmission,
    path: str | Path | None = None,
) -> None:
    """
    Save a submission's shift counts to history.

    Overwrites any existing entry for the same physician + month, so
    re-importing a corrected submission automatically updates the record.
    """
    history_path = Path(path) if path else _DEFAULT_HISTORY_PATH
    data = _load_raw(history_path)

    pid = submission.physician_id
    key = _month_key(submission.year, submission.month)

    if pid not in data:
        data[pid] = {}

    data[pid][key] = {
        "requested": submission.shifts_requested,
        "min": submission.shifts_min,
        "max": submission.shifts_max,
    }

    _save_raw(data, history_path)


def check_variance(
    submission: PhysicianSubmission,
    path: str | Path | None = None,
) -> list[ValidationIssue]:
    """
    Compare this month's requested shift count against recent history.

    Returns a warning ValidationIssue if the requested count deviates by
    more than _VARIANCE_THRESHOLD shifts from the recent rolling average.
    Returns an empty list if there is insufficient history to compare.
    """
    history_path = Path(path) if path else _DEFAULT_HISTORY_PATH
    data = _load_raw(history_path)

    pid = submission.physician_id
    current_key = _month_key(submission.year, submission.month)
    physician_history: dict = data.get(pid, {})

    # Collect past months only (exclude the current month if already recorded)
    past_entries = [
        v["requested"]
        for k, v in physician_history.items()
        if k != current_key
    ]

    # Sort chronologically and take the last _LOOKBACK_MONTHS
    all_keys = _sorted_keys({k: None for k in physician_history if k != current_key})
    recent_requested = [
        physician_history[k]["requested"]
        for k in all_keys[-_LOOKBACK_MONTHS:]
    ]

    if len(recent_requested) < 2:
        # Not enough history to establish a baseline
        return []

    avg = sum(recent_requested) / len(recent_requested)
    delta = abs(submission.shifts_requested - avg)

    if delta > _VARIANCE_THRESHOLD:
        direction = "above" if submission.shifts_requested > avg else "below"
        return [
            ValidationIssue(
                severity="warning",
                rule="shift_count_variance",
                message=(
                    f"Requested {submission.shifts_requested} shift(s) this month, "
                    f"which is {delta:.1f} shift(s) {direction} the recent "
                    f"{len(recent_requested)}-month average of {avg:.1f}."
                ),
                physician_id=pid,
            )
        ]

    return []


def get_history(
    physician_id: str,
    path: str | Path | None = None,
) -> dict[str, dict]:
    """
    Return the full month-by-month history for one physician.

    Keys are "YYYY-MM" strings; values are {"requested", "min", "max"}.
    """
    history_path = Path(path) if path else _DEFAULT_HISTORY_PATH
    data = _load_raw(history_path)
    return dict(data.get(physician_id, {}))


def summarise(
    physician_id: str,
    path: str | Path | None = None,
) -> str:
    """Return a human-readable history summary for one physician."""
    hist = get_history(physician_id, path)
    if not hist:
        return f"{physician_id}: no history recorded."
    lines = [f"{physician_id} shift history:"]
    for key in _sorted_keys(hist):
        e = hist[key]
        lines.append(
            f"  {key}  requested={e['requested']}  "
            f"min={e['min']}  max={e['max']}"
        )
    return "\n".join(lines)
