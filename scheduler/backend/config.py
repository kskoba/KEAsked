"""
Physician roster loader.

Reads config/physicians.yaml and provides:
  - PhysicianConfig  — per-physician preferences and rule overrides
  - load_roster()    — parse the YAML file into a dict keyed by physician ID
  - apply_config()   — merge a PhysicianConfig into a PhysicianSubmission
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    raise ImportError("PyYAML is required: pip install pyyaml")

from scheduler.backend.models import PhysicianSubmission


# Location of the roster file — respects CONFIG_DIR env var set by Electron
# when running as a packaged app, or sys._MEIPASS for PyInstaller bundles.
def _resolve_config_dir() -> Path:
    if os.environ.get("CONFIG_DIR"):
        return Path(os.environ["CONFIG_DIR"])
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config"
    return Path(__file__).parent.parent / "config"


_DEFAULT_ROSTER_PATH = _resolve_config_dir() / "physicians.yaml"


# Valid values for group_b_site_preference (Group B = RAH I, NEHC, RAH F)
GROUP_B_PREFS = frozenset({"nehc", "rah", "rah_f"})

# Canonical site names — must match shifts.py _SITE_TO_GROUP keys
VALID_SITES = frozenset({
    "NEHC",
    "RAH A side",
    "RAH B side",
    "RAH I side",
    "RAH F side",
})


@dataclass
class PhysicianConfig:
    """
    Static configuration for one physician, loaded from physicians.yaml.

    This is separate from PhysicianSubmission (which is derived from the
    monthly Excel file).  The two are merged before validation.
    """

    id: str
    name: str
    email: str = ""
    active: bool = True

    # Scheduling behaviour preferences (stable across months)
    max_consecutive_shifts: int = 3    # SIAR — max shifts in a row (any type)
    max_consecutive_nights: int = 3    # NIAR — max 2400h shifts in a row

    # Within-Group-B site preference for the 62% non-acute allocation.
    #   "nehc"  → prefer NEHC
    #   "rah"   → prefer RAH I or RAH F (generic RAH within Group B)
    #   "rah_f" → prefer specifically RAH F side
    #   None    → no preference; scheduler distributes freely within Group B
    group_b_site_preference: str | None = None

    # Sites this physician must never be assigned to.
    # Values must be canonical site names (see VALID_SITES).
    forbidden_sites: list[str] = field(default_factory=list)

    # Validation rule overrides.
    # Keys: "min_valid_days" | "min_valid_blocks" | "min_weekend_days" | "min_anchored_days"
    # Values: int (replacement threshold) | None (disable rule)
    rule_overrides: dict[str, int | None] = field(default_factory=dict)

    def describe_overrides(self) -> str:
        """Human-readable summary of non-standard rules."""
        if not self.rule_overrides:
            return "standard rules"
        parts = []
        for rule, value in self.rule_overrides.items():
            parts.append(f"{rule}={'disabled' if value is None else value}")
        return ", ".join(parts)


def _parse_physician(raw: dict) -> PhysicianConfig:
    """Parse one physician dict from the YAML into a PhysicianConfig."""
    sched: dict = raw.get("scheduling") or {}
    overrides_raw: dict = raw.get("rule_overrides") or {}

    # Normalise override values: keys must be known rule IDs,
    # values must be int or None.
    valid_rules = {
        "min_valid_days", "min_valid_blocks",
        "min_weekend_days", "min_anchored_days",
    }
    overrides: dict[str, int | None] = {}
    for key, val in overrides_raw.items():
        if key not in valid_rules:
            raise ValueError(
                f"Physician {raw.get('id')!r}: unknown rule override {key!r}. "
                f"Valid keys: {sorted(valid_rules)}"
            )
        overrides[key] = None if val is None else int(val)

    # group_b_site_preference
    raw_pref = raw.get("scheduling", {}).get("group_b_site_preference")
    if raw_pref is not None:
        raw_pref = str(raw_pref).lower().strip()
        if raw_pref not in GROUP_B_PREFS:
            raise ValueError(
                f"Physician {raw.get('id')!r}: invalid group_b_site_preference "
                f"{raw_pref!r}. Valid values: {sorted(GROUP_B_PREFS)}"
            )

    # forbidden_sites
    raw_forbidden: list = raw.get("forbidden_sites") or []
    forbidden_sites: list[str] = []
    for site in raw_forbidden:
        site_str = str(site).strip()
        if site_str not in VALID_SITES:
            raise ValueError(
                f"Physician {raw.get('id')!r}: unknown forbidden site {site_str!r}. "
                f"Valid sites: {sorted(VALID_SITES)}"
            )
        forbidden_sites.append(site_str)

    return PhysicianConfig(
        id=str(raw["id"]),
        name=str(raw["name"]),
        email=str(raw.get("email") or ""),
        active=bool(raw.get("active", True)),
        max_consecutive_shifts=int(sched.get("max_consecutive_shifts", 3)),
        max_consecutive_nights=int(
            sched.get("max_consecutive_nights", sched.get("max_consecutive_shifts", 3))
        ),
        group_b_site_preference=raw_pref,
        forbidden_sites=forbidden_sites,
        rule_overrides=overrides,
    )


def load_roster(
    path: str | Path | None = None,
) -> dict[str, PhysicianConfig]:
    """
    Parse physicians.yaml and return a dict keyed by physician ID.

    Parameters
    ----------
    path:
        Path to the YAML file.  Defaults to config/physicians.yaml
        relative to the scheduler package root.
    """
    roster_path = Path(path) if path else _DEFAULT_ROSTER_PATH
    with roster_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    physicians = data.get("physicians") or []
    roster: dict[str, PhysicianConfig] = {}
    for raw in physicians:
        cfg = _parse_physician(raw)
        if cfg.id in roster:
            raise ValueError(f"Duplicate physician ID {cfg.id!r} in {roster_path}")
        roster[cfg.id] = cfg

    return roster


def apply_config(
    submission: PhysicianSubmission,
    config: PhysicianConfig,
) -> PhysicianSubmission:
    """
    Merge a PhysicianConfig into a PhysicianSubmission.

    Writes rule_overrides from config into the submission so the validator
    picks them up automatically.  Returns the same submission object
    (mutated in place) for convenience.
    """
    submission.rule_overrides = dict(config.rule_overrides)
    return submission
