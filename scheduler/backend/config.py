"""
Physician roster loader.

Reads config/physicians.yaml and provides:
  - PhysicianConfig  — per-physician preferences and rule overrides
  - load_roster()    — parse the YAML file into a dict keyed by physician ID
  - apply_config()   — merge a PhysicianConfig into a PhysicianSubmission
  - save_physician()  — write one physician's fields back into the file
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    raise ImportError("PyYAML is required: pip install pyyaml")

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import LiteralScalarString

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

# Valid values for call_linkage (see PhysicianConfig.call_linkage below).
# Deliberately does NOT include a generic "before" option — a call shift's
# own start time can't be relied on, since an activation partway through
# the call window means the physician doesn't know until it happens how
# long they'll actually be working. "doc_before_evening" is the one
# narrow exception confirmed safe: DOC (0500h-1600h) immediately before a
# shift that starts in the evening (1600h/1800h/2000h) the next day always
# leaves enough of a gap even in the worst case (activated right at the
# end of the DOC window). NOC (1600h-0500h) is never offered before a
# block at all — an activation could run right up to the next shift's
# start with no predictable rest gap.
CALL_LINKAGE_VALUES = frozenset({"end_of_block", "doc_before_evening", "independent"})

# Valid values for avoid_weekday (generalizes the legacy avoid_mondays flag).
WEEKDAY_VALUES = frozenset({"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"})

# Canonical site names — must match shifts.py _SITE_TO_GROUP keys
VALID_SITES = frozenset({
    "NEHC",
    "RAH A side",
    "RAH B side",
    "RAH I side",
    "RAH F side",
})

# Valid keys for a physician's rule_overrides dict — see PhysicianConfig
# below for what each one means.
VALID_RULE_OVERRIDES = frozenset({
    "min_valid_days", "min_valid_blocks",
    "min_weekend_days", "min_anchored_days",
    "min_blocks_per_day",
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

    # Free-text scheduling notes about this physician (e.g. a standing
    # restriction, a note for whoever reviews shift-request deficiencies).
    # Purely informational — nothing in the scheduler reads this field.
    notes: str = ""

    # Structured name parts for display formatting (e.g. "Lastname, F").
    # Not always fully known — first_name may be empty (surname-only on
    # file), an initial ("F"), or a full first name ("Amanda"), depending
    # on what's actually recorded for this physician.
    last_name: str = ""
    first_name: str = ""

    # Other id/name strings this physician might appear under in imported
    # submission files (e.g. a flat-file sheet using "Hanson A" for the
    # physician whose canonical roster name is "Amanda Hanson"). Matched
    # case-insensitively by physician_resolver.py — see that module for
    # why this is exact-match only, never fuzzy.
    aliases: list[str] = field(default_factory=list)

    # Scheduling behaviour preferences (stable across months)
    max_consecutive_shifts: int = 3    # SIAR — max shifts in a row (any type)
    max_consecutive_nights: int = 3    # NIAR — max 2400h shifts in a row

    # Standing annual-survey baseline: roughly how many shifts/month, and
    # how many 0600h/2400h shifts specifically, this physician says they
    # typically want (+/- 1-2 shifts), independent of any single month's
    # actual submission. Informational/reference only — nothing in the
    # scheduler reads these to constrain a solve. Distinct from
    # default_shifts_requested below, which *does* actively override a
    # month's submitted count; these three are just what the person told
    # us to expect, for sanity-checking a given month's numbers against.
    # None (default) means not yet recorded from a survey.
    typical_shifts_per_month: Optional[int] = None
    typical_0600h_per_month: Optional[int] = None
    typical_2400h_per_month: Optional[int] = None

    # Within-Group-B site preference for the 62% non-acute allocation.
    #   "nehc"  → prefer NEHC
    #   "rah"   → prefer RAH I or RAH F (generic RAH within Group B)
    #   "rah_f" → prefer specifically RAH F side
    #   None    → no preference; scheduler distributes freely within Group B
    group_b_site_preference: str | None = None

    # Sites this physician must never be assigned to.
    # Values must be canonical site names (see VALID_SITES).
    forbidden_sites: list[str] = field(default_factory=list)

    # Shift-type restriction: if True, physician may only be assigned 2400h shifts.
    only_2400h: bool = False

    # Symmetric counterpart to only_2400h: if True, physician may only be
    # assigned 0600h shifts. Added from the Sept 2026 preferences survey --
    # only_2400h existed but nothing let a 0600h-only physician (e.g. Deol,
    # Mrochuk) express the same restriction.
    only_0600h: bool = False

    # Soft scheduling preferences.
    prefer_weekends: bool = False

    # Per-physician weekend cap; if set, overrides the proportional
    # max_weekends_per_month calculation entirely (see generator.py's
    # _eff_max_weekends). Use for a standing exception like someone who
    # works every weekend regardless of shift count.
    max_weekends: Optional[int] = None

    # If True, requested dates+shifts are treated as near-mandatory (high score bonus).
    honor_all_requests: bool = False

    # If True, solver will not penalise isolated 2400h nights for this physician
    # (i.e. singleton midnight shifts are acceptable / preferred).
    prefer_singleton_nights: bool = False

    # Opposite of prefer_singleton_nights: if True, the solver specifically
    # rewards a night-shift run that reaches this physician's own full
    # max_consecutive_nights length, not just any adjacent pair. Added for
    # KLam (2026-09-29), who wants 4 consecutive 2400h nights rather than
    # shorter clusters. The generic pair-clustering bonus everyone already
    # gets isn't always assertive enough on its own to prefer a full-length
    # run over several shorter ones, so this adds a direct, larger bonus
    # for physicians who've said the full run is what they want.
    #
    # A genuine hard-constraint bug (now fixed, see HC-9's gap==2/3 loop in
    # generator_cpsat.py) used to make ANY 3+-night consecutive run
    # mathematically infeasible for every physician -- the gap-2 "late
    # shift forces a rest day" check didn't account for the day in between
    # itself being a continuing night shift. If a physician with this flag
    # still isn't landing full-length runs in a real joint solve, that's
    # now a weight-tuning question (this bonus losing out to other
    # objective terms, e.g. prefer_weekends day-specific incentives), not
    # a structural infeasibility -- confirm with a solo/uncontested test
    # before assuming otherwise.
    prefer_clustered_nights: bool = False

    # Shift-time restrictions: physician may never be assigned shifts at these
    # start times (e.g. ["0600h", "2400h"]).
    forbidden_shift_times: list[str] = field(default_factory=list)

    # If True, physician cannot be assigned AM CALL (DOC) or PM CALL (NOC).
    no_call: bool = False

    # Soft preference: penalise Monday assignments (e.g. administrative days).
    avoid_mondays: bool = False

    # If True, physician must have the day off after any shift at 1600h, 1800h,
    # or 2000h (late-shift rest privilege).
    rest_after_late_shift: bool = False

    # Max consecutive days that may have an 1800h shift (default 3 = no real
    # limit for most; set to 2 for physicians who cannot work 1800h three days
    # in a row).
    max_consecutive_1800h: int = 3

    # If True, hard-cap total shifts at shifts_requested rather than shifts_max.
    cap_at_requested: bool = False

    # If True, this physician has a standing scheduling limitation serious
    # enough that their monthly submission's validation errors are expected
    # and should be auto-overridden at import time rather than making a
    # human click through them every month (e.g. Francescutti/Krisik — a
    # site or shift-type restriction that will always fail the generic
    # min_valid_days/min_valid_blocks/etc. thresholds). Overridden issues
    # still show up in the Validate page's expandable detail, just already
    # marked resolved. Independent of casual below — a physician can be
    # either, both, or neither.
    special_provisions: bool = False

    # If True, this physician is casual staff: also auto-overridden like
    # special_provisions, but additionally deprioritized by the scheduler —
    # only assigned shifts once every non-casual physician has reached
    # their own requested shift count (not their max), and only into slots
    # still open after that. See generator.py/generator_cpsat.py's casual
    # handling for the actual priority ordering.
    casual: bool = False

    # If set, this physician's monthly shifts_requested is always treated
    # as this value, overriding whatever their submission actually says
    # (including a blank/zero submission). For a physician whose submitted
    # count is unreliable but who should still get normal scheduling
    # priority every month (e.g. MacGougan — always request 10). None means
    # no override; use whatever the submission reports, as normal.
    default_shifts_requested: Optional[int] = None

    # Number of real people sharing this single roster identity (e.g.
    # LamRico = Kenneth Lam + Michelle Rico, each submitting their own
    # identical-values sheet under aliases that both resolve to this one
    # id). Only one submission survives import-time id resolution, so its
    # shifts_requested/min/max are multiplied by this factor to reflect the
    # combined capacity of every person behind the identity. 1 (default)
    # means a normal single-person identity — no scaling.
    combined_headcount: int = 1

    # Multiplier on this physician's requested-count scheduling priority
    # (see generator_cpsat.py's per-physician requested-count bonus). 1.0
    # (default) is normal priority. Use > 1.0 for a physician who should
    # get more say over reaching their own requested count than the
    # general population — e.g. a department chief (Haager, MacGougan)
    # whose preferences carry more institutional weight. Never applies to
    # a casual physician regardless of this value — casual physicians stay
    # in their own separate, strictly-lower priority tier (see `casual`).
    priority_weight: float = 1.0

    # Post-block cool-down: if this physician works a stretch of at least
    # post_block_min_length consecutive days, they must have the following
    # post_block_rest_days entirely off before working again. 0 (default)
    # disables the rule regardless of post_block_min_length. Generalizes
    # rest_after_late_shift (which only triggers on specific shift TIMES)
    # to trigger on block LENGTH instead — from the Sept 2026 preferences
    # survey, where this was the single most-requested new rule (~14
    # respondents, e.g. "48 hours off after nights is essential").
    post_block_rest_days: int = 0
    post_block_min_length: int = 2

    # On-call (DOC/NOC) placement preference relative to this physician's
    # own regular-shift blocks. One of CALL_LINKAGE_VALUES, or None (no
    # preference — current default greedy behaviour, unchanged).
    #   "end_of_block"      — prefer the call day immediately after their
    #                          last regular shift in a stretch.
    #   "doc_before_evening" — the one safe "before a block" case: a DOC
    #                          call the day immediately before a shift
    #                          starting in the evening. See
    #                          CALL_LINKAGE_VALUES above for why this is
    #                          the only "before" option offered.
    #   "independent"        — prefer a call day untouched by any of their
    #                          own regular shifts on either side.
    call_linkage: Optional[str] = None

    # Hard cap: no more than N consecutive days at the same site (e.g.
    # "no 2 Intake shifts in a row"). None (default) = no constraint.
    max_consecutive_same_site: Optional[int] = None

    # Weekday this physician wants avoided (e.g. a protected admin day) —
    # one of WEEKDAY_VALUES, or None. Soft preference (same -5/shift
    # objective penalty as the legacy avoid_mondays below). Generalizes
    # avoid_mondays to any single weekday; avoid_mondays still works as
    # shorthand for avoid_weekday="MON" when avoid_weekday itself is unset
    # — kept for backward compatibility rather than migrating every
    # existing avoid_mondays entry.
    avoid_weekday: Optional[str] = None

    # Soft preference: concentrate this physician's weekend shifts onto as
    # few distinct weekends as possible (e.g. one Fri/Sat/Sun stretch)
    # rather than spreading them thin across many weekends — distinct from
    # max_weekends, which caps the *count* of weekends touched but doesn't
    # otherwise prefer fewer of them when the count is already under cap.
    prefer_weekend_clumping: bool = False

    # Manual override for which anchor shift type (2400h or 0600h) this
    # physician prefers, when they should have to absorb an anchor-shift
    # overage (see generator_cpsat.py's anchor_overage_penalty_terms).
    # None (default) means no override — fall back to inferring it from
    # each month's own submission instead (explicit 0 for one type and a
    # real positive request for the other is a clear signal even without
    # a roster-level toggle). Set this only when that per-month inference
    # isn't enough, e.g. a standing preference that should hold regardless
    # of what a given month's numbers happen to look like. Valid values:
    # "2400h", "0600h", or None.
    anchor_preference: Optional[str] = None

    # Validation rule overrides.
    # Keys: "min_valid_days" | "min_valid_blocks" | "min_weekend_days" | "min_anchored_days"
    #     | "min_blocks_per_day" (per-day block threshold for what counts as a
    #       valid day at all — lower to 1 for physicians restricted to a single
    #       shift type per day, e.g. only_2400h; see validator.day_block_threshold)
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


_CALL_LINKAGE_TEXT = {
    "end_of_block": "Your on-call (DOC/NOC) days are scheduled right after your last regular shift in a stretch, when possible.",
    "doc_before_evening": "A day-on-call (DOC) day is scheduled the day before a shift starting in the evening, when possible.",
    "independent": "Your on-call (DOC/NOC) days are scheduled apart from your own regular shifts, when possible.",
}

_ANCHOR_PREFERENCE_TEXT = {
    "2400h": "If you go over your requested count of 0600h/2400h shifts, the extra ones are given as 2400h shifts.",
    "0600h": "If you go over your requested count of 0600h/2400h shifts, the extra ones are given as 0600h shifts.",
}


def describe_physician_facing_rules(cfg: "PhysicianConfig") -> list[str]:
    """
    Plain-language, physician-facing summary of what's actually configured
    for this physician in physicians.yaml -- pushed to sked's "My Rules"
    view on every roster save (see server.py + sked_client.push_physician_rules).

    Deliberately filtered: internal solver-tuning knobs a physician has no
    reason to see (combined_headcount, default_shifts_requested,
    rule_overrides, cap_at_requested, casual/special_provisions status,
    coordinator's own free-text notes) are left out. prefer_weekend_clumping
    is also left out -- it's applied to everyone now regardless of its
    value (see generator_cpsat.py's own comment on that), so showing it
    per-physician would be actively misleading. max_consecutive_1800h is
    left out too since it's no longer independently set -- it always tracks
    max_consecutive_shifts now, so it'd just be a confusing duplicate line.

    max_consecutive_shifts (SIAR) and max_consecutive_nights (NIAR) are
    always shown, even at their default value, since physicians clearly
    want to see their actual caps rather than only being told about
    deviations. Most other fields are only surfaced when they differ from
    the plain default -- a physician with nothing else unusual configured
    just gets those two lines, not a wall of baseline values everyone
    shares.
    """
    items: list[str] = []

    items.append(f"You can be scheduled up to {cfg.max_consecutive_shifts} shift(s) in a row.")
    if cfg.max_consecutive_nights > 0:
        items.append(f"You can be scheduled up to {cfg.max_consecutive_nights} 2400h (night) shift(s) in a row.")
    else:
        items.append("You are never scheduled for a 2400h (night) shift at all — the cap is set to zero.")
    if cfg.only_2400h:
        items.append("You're only ever assigned 2400h (night) shifts.")
    if cfg.only_0600h:
        items.append("You're only ever assigned 0600h shifts.")
    if cfg.group_b_site_preference:
        site_text = {"nehc": "NEHC", "rah": "RAH (I or F side)", "rah_f": "RAH F side"}.get(
            cfg.group_b_site_preference, cfg.group_b_site_preference
        )
        items.append(f"Within the non-acute allocation, you're preferentially scheduled at {site_text}.")
    if cfg.forbidden_sites:
        items.append(f"You're never scheduled at: {', '.join(cfg.forbidden_sites)}.")
    if cfg.forbidden_shift_times:
        items.append(f"You're never scheduled for these shift times: {', '.join(cfg.forbidden_shift_times)}.")
    if cfg.prefer_weekends:
        items.append(
            "You've indicated you prefer weekend shifts — the scheduler actively favors giving you more "
            "weekend work and avoids leaving you with just a Friday or Sunday shift and a gap on Saturday."
        )
    if cfg.max_weekends is not None and not cfg.prefer_weekends:
        # prefer_weekends unconditionally waives this cap (see
        # generator_cpsat.py's _eff_max_weekends) -- showing it while that
        # flag is also set would misstate an actual cap that doesn't apply.
        items.append(f"You're capped at {cfg.max_weekends} weekend(s) worked per month.")
    if cfg.honor_all_requests:
        items.append("Your specific date/shift requests are treated as close to mandatory.")
    if cfg.priority_weight > 1.0:
        items.append(
            "Your requested shifts are given extra weight over a colleague's when the schedule can't "
            "accommodate everyone's requests in full — you're prioritized for reaching your own requested count."
        )
    if cfg.prefer_singleton_nights:
        items.append("You prefer isolated single night shifts rather than several in a row.")
    if cfg.prefer_clustered_nights:
        items.append(
            f"You've indicated you prefer a full run of {cfg.max_consecutive_nights} 2400h (night) shifts "
            "in a row over shorter, separate clusters — the scheduler specifically rewards completing the full run."
        )
    if cfg.no_call:
        items.append("You're never assigned on-call (DOC/NOC) shifts.")
    if cfg.avoid_weekday:
        items.append(f"Scheduling tries to avoid putting you on {cfg.avoid_weekday}s.")
    elif cfg.avoid_mondays:
        items.append("Scheduling tries to avoid putting you on Mondays.")
    if cfg.rest_after_late_shift:
        items.append("You're guaranteed a day off after any shift starting at 1600h, 1800h, or 2000h.")
    if cfg.post_block_rest_days:
        items.append(
            f"After working {cfg.post_block_min_length}+ days in a row, you're guaranteed "
            f"{cfg.post_block_rest_days} day(s) off before working again."
        )
    if cfg.call_linkage and cfg.call_linkage in _CALL_LINKAGE_TEXT:
        items.append(_CALL_LINKAGE_TEXT[cfg.call_linkage])
    if cfg.max_consecutive_same_site is not None:
        items.append(f"You're never scheduled more than {cfg.max_consecutive_same_site} day(s) in a row at the same site.")
    if cfg.anchor_preference and cfg.anchor_preference in _ANCHOR_PREFERENCE_TEXT:
        items.append(_ANCHOR_PREFERENCE_TEXT[cfg.anchor_preference])

    if cfg.typical_shifts_per_month is not None or cfg.typical_0600h_per_month is not None or cfg.typical_2400h_per_month is not None:
        bits = []
        if cfg.typical_shifts_per_month is not None:
            bits.append(f"{cfg.typical_shifts_per_month} shifts/month")
        if cfg.typical_0600h_per_month is not None:
            bits.append(f"{cfg.typical_0600h_per_month} 0600h shifts/month")
        if cfg.typical_2400h_per_month is not None:
            bits.append(f"{cfg.typical_2400h_per_month} 2400h shifts/month")
        items.append("On file as your typical monthly baseline from the annual survey: " + ", ".join(bits) + ".")

    return items


def _parse_physician(raw: dict) -> PhysicianConfig:
    """Parse one physician dict from the YAML into a PhysicianConfig."""
    sched: dict = raw.get("scheduling") or {}
    overrides_raw: dict = raw.get("rule_overrides") or {}

    # Normalise override values: keys must be known rule IDs,
    # values must be int or None.
    overrides: dict[str, int | None] = {}
    for key, val in overrides_raw.items():
        if key not in VALID_RULE_OVERRIDES:
            raise ValueError(
                f"Physician {raw.get('id')!r}: unknown rule override {key!r}. "
                f"Valid keys: {sorted(VALID_RULE_OVERRIDES)}"
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

    raw_max_weekends = sched.get("max_weekends")
    parsed_max_weekends: Optional[int] = (
        int(raw_max_weekends) if raw_max_weekends is not None else None
    )

    # forbidden_shift_times
    raw_forbidden_times: list = sched.get("forbidden_shift_times") or []
    forbidden_shift_times = [str(t).strip() for t in raw_forbidden_times]

    raw_aliases: list = raw.get("aliases") or []
    aliases = [str(a).strip() for a in raw_aliases if str(a).strip()]

    raw_call_linkage = sched.get("call_linkage")
    if raw_call_linkage is not None:
        raw_call_linkage = str(raw_call_linkage).strip()
        if raw_call_linkage not in CALL_LINKAGE_VALUES:
            raise ValueError(
                f"Physician {raw.get('id')!r}: invalid call_linkage {raw_call_linkage!r}. "
                f"Valid values: {sorted(CALL_LINKAGE_VALUES)}"
            )

    raw_avoid_weekday = sched.get("avoid_weekday")
    if raw_avoid_weekday is not None:
        raw_avoid_weekday = str(raw_avoid_weekday).strip().upper()
        if raw_avoid_weekday not in WEEKDAY_VALUES:
            raise ValueError(
                f"Physician {raw.get('id')!r}: invalid avoid_weekday {raw_avoid_weekday!r}. "
                f"Valid values: {sorted(WEEKDAY_VALUES)}"
            )

    raw_max_same_site = sched.get("max_consecutive_same_site")
    parsed_max_same_site: Optional[int] = (
        int(raw_max_same_site) if raw_max_same_site is not None else None
    )

    return PhysicianConfig(
        id=str(raw["id"]),
        name=str(raw["name"]),
        email=str(raw.get("email") or ""),
        notes=str(raw.get("notes") or ""),
        active=bool(raw.get("active", True)),
        aliases=aliases,
        last_name=str(raw.get("last_name") or ""),
        first_name=str(raw.get("first_name") or ""),
        max_consecutive_shifts=int(sched.get("max_consecutive_shifts", 3)),
        max_consecutive_nights=int(
            sched.get("max_consecutive_nights", sched.get("max_consecutive_shifts", 3))
        ),
        typical_shifts_per_month=(
            int(sched["typical_shifts_per_month"])
            if sched.get("typical_shifts_per_month") is not None
            else None
        ),
        typical_0600h_per_month=(
            int(sched["typical_0600h_per_month"])
            if sched.get("typical_0600h_per_month") is not None
            else None
        ),
        typical_2400h_per_month=(
            int(sched["typical_2400h_per_month"])
            if sched.get("typical_2400h_per_month") is not None
            else None
        ),
        group_b_site_preference=raw_pref,
        forbidden_sites=forbidden_sites,
        rule_overrides=overrides,
        only_2400h=bool(sched.get("only_2400h", False)),
        only_0600h=bool(sched.get("only_0600h", False)),
        prefer_weekends=bool(sched.get("prefer_weekends", False)),
        max_weekends=parsed_max_weekends,
        honor_all_requests=bool(sched.get("honor_all_requests", False)),
        prefer_singleton_nights=bool(sched.get("prefer_singleton_nights", False)),
        prefer_clustered_nights=bool(sched.get("prefer_clustered_nights", False)),
        forbidden_shift_times=forbidden_shift_times,
        no_call=bool(sched.get("no_call", False)),
        avoid_mondays=bool(sched.get("avoid_mondays", False)),
        rest_after_late_shift=bool(sched.get("rest_after_late_shift", False)),
        max_consecutive_1800h=int(sched.get("max_consecutive_1800h", 3)),
        cap_at_requested=bool(sched.get("cap_at_requested", False)),
        special_provisions=bool(sched.get("special_provisions", False)),
        casual=bool(sched.get("casual", False)),
        default_shifts_requested=(
            int(sched["default_shifts_requested"])
            if sched.get("default_shifts_requested") is not None
            else None
        ),
        combined_headcount=int(sched.get("combined_headcount", 1)),
        priority_weight=float(sched.get("priority_weight", 1.0)),
        anchor_preference=(
            sched["anchor_preference"]
            if sched.get("anchor_preference") in ("2400h", "0600h")
            else None
        ),
        post_block_rest_days=int(sched.get("post_block_rest_days", 0)),
        post_block_min_length=int(sched.get("post_block_min_length", 2)),
        call_linkage=raw_call_linkage,
        max_consecutive_same_site=parsed_max_same_site,
        avoid_weekday=raw_avoid_weekday,
        prefer_weekend_clumping=bool(sched.get("prefer_weekend_clumping", False)),
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


def physician_config_to_raw(cfg: PhysicianConfig) -> dict:
    """
    Inverse of _parse_physician: the plain nested dict `cfg` would parse
    from if it were read out of physicians.yaml, omitting fields left at
    their default value — matching the hand-authored style of the existing
    file. Used by save_physician() to merge an edit back into the file,
    and to round-trip a value through _parse_physician() for validation
    before it's written.
    """
    raw: dict = {
        "id": cfg.id,
        "name": cfg.name,
        "last_name": cfg.last_name,
        "first_name": cfg.first_name,
        "active": cfg.active,
    }
    if cfg.email:
        raw["email"] = cfg.email
    if cfg.notes:
        # Literal block style ("|") for multi-line notes so they read as
        # hand-written YAML rather than an escaped "\n"-laden string.
        raw["notes"] = LiteralScalarString(cfg.notes) if "\n" in cfg.notes else cfg.notes
    if cfg.aliases:
        raw["aliases"] = list(cfg.aliases)

    sched: dict = {"max_consecutive_shifts": cfg.max_consecutive_shifts}
    if cfg.max_consecutive_nights != cfg.max_consecutive_shifts:
        sched["max_consecutive_nights"] = cfg.max_consecutive_nights
    if cfg.typical_shifts_per_month is not None:
        sched["typical_shifts_per_month"] = cfg.typical_shifts_per_month
    if cfg.typical_0600h_per_month is not None:
        sched["typical_0600h_per_month"] = cfg.typical_0600h_per_month
    if cfg.typical_2400h_per_month is not None:
        sched["typical_2400h_per_month"] = cfg.typical_2400h_per_month
    if cfg.group_b_site_preference:
        sched["group_b_site_preference"] = cfg.group_b_site_preference
    if cfg.only_2400h:
        sched["only_2400h"] = True
    if cfg.only_0600h:
        sched["only_0600h"] = True
    if cfg.prefer_weekends:
        sched["prefer_weekends"] = True
    if cfg.max_weekends is not None:
        sched["max_weekends"] = cfg.max_weekends
    if cfg.honor_all_requests:
        sched["honor_all_requests"] = True
    if cfg.prefer_singleton_nights:
        sched["prefer_singleton_nights"] = True
    if cfg.prefer_clustered_nights:
        sched["prefer_clustered_nights"] = True
    if cfg.forbidden_shift_times:
        sched["forbidden_shift_times"] = list(cfg.forbidden_shift_times)
    if cfg.no_call:
        sched["no_call"] = True
    if cfg.avoid_mondays:
        sched["avoid_mondays"] = True
    if cfg.rest_after_late_shift:
        sched["rest_after_late_shift"] = True
    if cfg.max_consecutive_1800h != 3:
        sched["max_consecutive_1800h"] = cfg.max_consecutive_1800h
    if cfg.cap_at_requested:
        sched["cap_at_requested"] = True
    if cfg.special_provisions:
        sched["special_provisions"] = True
    if cfg.casual:
        sched["casual"] = True
    if cfg.default_shifts_requested is not None:
        sched["default_shifts_requested"] = cfg.default_shifts_requested
    if cfg.combined_headcount != 1:
        sched["combined_headcount"] = cfg.combined_headcount
    if cfg.priority_weight != 1.0:
        sched["priority_weight"] = cfg.priority_weight
    if cfg.anchor_preference in ("2400h", "0600h"):
        sched["anchor_preference"] = cfg.anchor_preference
    if cfg.post_block_rest_days:
        sched["post_block_rest_days"] = cfg.post_block_rest_days
        if cfg.post_block_min_length != 2:
            sched["post_block_min_length"] = cfg.post_block_min_length
    if cfg.call_linkage:
        sched["call_linkage"] = cfg.call_linkage
    if cfg.max_consecutive_same_site is not None:
        sched["max_consecutive_same_site"] = cfg.max_consecutive_same_site
    if cfg.avoid_weekday:
        sched["avoid_weekday"] = cfg.avoid_weekday
    if cfg.prefer_weekend_clumping:
        sched["prefer_weekend_clumping"] = True
    raw["scheduling"] = sched

    if cfg.forbidden_sites:
        raw["forbidden_sites"] = list(cfg.forbidden_sites)

    raw["rule_overrides"] = dict(cfg.rule_overrides)
    return raw


def _merge_mapping(entry: CommentedMap, desired: dict, nested_keys: tuple[str, ...] = ()) -> None:
    """
    Apply `desired` onto an existing ruamel CommentedMap `entry`, key by
    key, in place — updating the value of a key that already exists
    (which leaves any comment attached to that key alone) rather than
    clearing and rebuilding the map, adding keys that are newly needed,
    and removing keys that are no longer wanted. Keys named in
    `nested_keys` are treated as sub-mappings and merged recursively the
    same way instead of being replaced outright.

    Single pass over `desired` in its own key order, rather than nested
    keys first / flat keys second: a key that already exists in `entry`
    never moves (assigning to an existing CommentedMap key doesn't change
    its position), but a key that's genuinely new gets appended wherever
    this pass reaches it — which only actually matters when `entry` starts
    out empty (add_physician's brand-new record), where it's the only
    thing that makes the result come out in physician_config_to_raw's
    intended field order instead of nested-keys-first.
    """
    for key, value in desired.items():
        if key in nested_keys:
            sub_entry = entry.get(key)
            if not isinstance(sub_entry, CommentedMap):
                sub_entry = CommentedMap()
                entry[key] = sub_entry
            _merge_mapping(sub_entry, value)
        elif key in entry and entry[key] == value:
            continue  # leave untouched — preserves this key's original style/comment
        else:
            entry[key] = value

    for key in [k for k in list(entry.keys()) if k not in desired]:
        del entry[key]


def _open_roster_yaml(path: str | Path | None) -> tuple[YAML, CommentedMap, Path]:
    """
    Shared setup for every function that writes physicians.yaml: a
    round-trip YAML() configured to match the file's existing indent style
    ("  - id: ..." with content at column 4 — ruamel's indent width is an
    emitter-wide setting, not inferred per-node, so this must be set
    explicitly or every entry gets reformatted on dump, not just the ones
    actually touched), plus the parsed document.
    """
    roster_path = Path(path) if path else _DEFAULT_ROSTER_PATH
    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.width = 4096  # don't let comments/long lines force rewrapping
    yaml_rt.indent(mapping=2, sequence=4, offset=2)

    with roster_path.open(encoding="utf-8") as fh:
        data = yaml_rt.load(fh)

    return yaml_rt, data, roster_path


def save_physician(
    physician_id: str,
    cfg: PhysicianConfig,
    path: str | Path | None = None,
) -> None:
    """
    Write one physician's fields back into physicians.yaml, in place.

    Uses ruamel.yaml's round-trip mode and mutates the existing entry's
    keys rather than replacing it wholesale, so hand-written comments
    elsewhere in the file — including ones attached to this physician's
    own fields, e.g. "max_weekends: 5  # works all weekends..." — survive
    the edit, and every other physician's entry is untouched byte-for-byte.

    This only edits an existing physician; raises KeyError if `physician_id`
    isn't already in the file (use add_physician() for a brand new one).
    """
    yaml_rt, data, roster_path = _open_roster_yaml(path)

    physicians = data.get("physicians") or []
    entry = next((p for p in physicians if str(p.get("id")) == physician_id), None)
    if entry is None:
        raise KeyError(f"No physician with id {physician_id!r} in {roster_path}")

    desired = physician_config_to_raw(cfg)
    desired["id"] = physician_id  # id is the lookup key, never editable

    _merge_mapping(entry, desired, nested_keys=("scheduling",))

    with roster_path.open("w", encoding="utf-8") as fh:
        yaml_rt.dump(data, fh)


def add_physician(cfg: PhysicianConfig, path: str | Path | None = None) -> None:
    """
    Insert a new physician into physicians.yaml, positioned alphabetically
    by id (case-insensitive) to match the file's existing ordering.
    Raises ValueError if cfg.id is already in use.
    """
    yaml_rt, data, roster_path = _open_roster_yaml(path)

    physicians = data["physicians"]
    if any(str(p.get("id")) == cfg.id for p in physicians):
        raise ValueError(f"Physician id {cfg.id!r} already exists in {roster_path}")

    entry = CommentedMap()
    _merge_mapping(entry, physician_config_to_raw(cfg), nested_keys=("scheduling",))

    insert_at = len(physicians)
    for idx, p in enumerate(physicians):
        if str(p.get("id", "")).casefold() > cfg.id.casefold():
            insert_at = idx
            break
    physicians.insert(insert_at, entry)

    with roster_path.open("w", encoding="utf-8") as fh:
        yaml_rt.dump(data, fh)


def remove_physician(physician_id: str, path: str | Path | None = None) -> None:
    """Delete one physician's entry from physicians.yaml. Raises KeyError if not found."""
    yaml_rt, data, roster_path = _open_roster_yaml(path)

    physicians = data.get("physicians") or []
    idx = next((i for i, p in enumerate(physicians) if str(p.get("id")) == physician_id), None)
    if idx is None:
        raise KeyError(f"No physician with id {physician_id!r} in {roster_path}")
    del physicians[idx]

    with roster_path.open("w", encoding="utf-8") as fh:
        yaml_rt.dump(data, fh)


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
