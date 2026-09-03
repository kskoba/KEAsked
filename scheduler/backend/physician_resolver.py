"""
Physician identity resolution.

The same physician can appear under different name/ID strings across input
sources — a per-physician xlsx filename, a name typed into a flat submission
file, etc. (e.g. "Amanda Hanson" vs "Hanson A"). This module is the single
place that maps any such variant string to the canonical roster ID, using
physicians.yaml's `id`, `name`, and `aliases` fields.

Deliberately exact-match only (case-insensitive, whitespace-normalized) — no
fuzzy/similarity matching. A wrong guess here means a shift could be silently
assigned under the wrong identity, so an unresolved name must surface as a
validation error for a human to fix (by adding an alias to physicians.yaml),
never be auto-guessed.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional

from scheduler.backend.config import PhysicianConfig


def _normalize(s: str) -> str:
    # Commas and periods are treated as whitespace so "Williamson, Ian" and
    # "J. Gunawan" normalize the same as "Williamson Ian" / "J Gunawan".
    cleaned = str(s).replace(",", " ").replace(".", " ")
    return " ".join(cleaned.split()).casefold()


def build_alias_index(roster: dict[str, PhysicianConfig]) -> dict[str, str]:
    """
    Build a normalized-variant -> canonical-roster-id lookup.

    Matchable variants per physician:
      - id, name, aliases, as literally recorded in physicians.yaml
      - "First Last", "Last First", "Last I", "I Last", and the same four
        forms with no space (e.g. "FBrown", "BrownF") — derived from the
        structured last_name/first_name fields, when both are known, to
        cover submission files that use a different name order/format
        than whatever happens to be in `name` (e.g. roster name "Yeung
        Alex" but a submission says "Alex Yeung") or a filename-stem-style
        id with no separator (e.g. roster id "BrownF" but a submission
        file is named "FBrown.xlsx")
      - bare last_name alone — but ONLY when that surname is unique across
        the whole roster (e.g. "Skoblenick"); a shared surname like
        "Chang" or "Yeung" is deliberately never added bare, since which
        physician it means would be a guess, not a lookup

    If two different physicians would ever produce the identical
    normalized variant, that variant is dropped entirely rather than
    resolving to whichever physician happened to be processed first — an
    unresolved name is safe (surfaces as a validation error); a silently
    wrong match is not.

    Literal fields (id/name/aliases) always take priority over derived
    variants: e.g. AYeung (Aref) and YeungAlex (Alex) share first initial
    "A", so YeungAlex's derived no-space "Initial+Surname" form is also
    the literal string "AYeung" — that must never be allowed to knock out
    AYeung's own id resolving to itself, so derived variants are only
    added where no literal field has already claimed that key.

    Build once per roster and reuse across a batch of lookups (e.g. one
    import run) rather than rebuilding per-submission.
    """
    last_name_counts = Counter(
        _normalize(cfg.last_name) for cfg in roster.values() if cfg.last_name
    )

    def collect(pairs: list[tuple[str, str]]) -> dict[str, str]:
        claims: dict[str, set[str]] = {}
        for variant, pid in pairs:
            key = _normalize(variant)
            if key:
                claims.setdefault(key, set()).add(pid)
        return {key: next(iter(ids)) for key, ids in claims.items() if len(ids) == 1}

    literal_pairs: list[tuple[str, str]] = []
    derived_pairs: list[tuple[str, str]] = []

    for cfg in roster.values():
        literal_pairs.append((cfg.id, cfg.id))
        literal_pairs.append((cfg.name, cfg.id))
        for alias in cfg.aliases:
            literal_pairs.append((alias, cfg.id))

        if cfg.last_name and cfg.first_name:
            first, last, initial = cfg.first_name, cfg.last_name, cfg.first_name[0]
            for a, b in ((first, last), (last, first), (last, initial), (initial, last)):
                derived_pairs.append((f"{a} {b}", cfg.id))
                derived_pairs.append((f"{a}{b}", cfg.id))

        if cfg.last_name and last_name_counts[_normalize(cfg.last_name)] == 1:
            derived_pairs.append((cfg.last_name, cfg.id))

    index = collect(literal_pairs)
    for key, pid in collect(derived_pairs).items():
        index.setdefault(key, pid)
    return index


def resolve_physician_id(raw: str, index: dict[str, str]) -> Optional[str]:
    """
    Resolve a raw id/name string to its canonical roster id using a
    pre-built alias index (see build_alias_index).

    Returns None if `raw` doesn't match anything in the roster — callers
    must treat that as an unresolved identity, not silently fall back to
    using `raw` as-is.
    """
    if not raw:
        return None
    return index.get(_normalize(raw))


def sort_key(cfg: PhysicianConfig) -> tuple[str, str]:
    """Sort key for listing physicians by last name (then first name/initial)."""
    return (cfg.last_name.casefold(), cfg.first_name.casefold())


def build_display_names(roster: dict[str, PhysicianConfig]) -> dict[str, str]:
    """
    Compute a "Lastname, F" display name for every physician in the roster.

    Uses just the first initial by default. Falls back to the full first
    name only for physicians whose "Lastname, Initial" form collides with
    another physician's (e.g. "Yeung, Aref" and "Yeung, Alex" both reduce
    to "Yeung, A") — and only when the full first name is actually known;
    if it isn't, the bare last name is the best available fallback.

    A physician with no last_name on file (shouldn't normally happen once
    physicians.yaml is fully populated) falls back to `name` as-is.
    """
    def initial_form(cfg: PhysicianConfig) -> str:
        if not cfg.last_name:
            return cfg.name
        if cfg.first_name:
            return f"{cfg.last_name}, {cfg.first_name[0]}"
        return cfg.last_name

    candidates = {pid: initial_form(cfg) for pid, cfg in roster.items()}
    counts = Counter(candidates.values())

    display: dict[str, str] = {}
    for pid, cfg in roster.items():
        candidate = candidates[pid]
        if counts[candidate] > 1 and cfg.first_name:
            display[pid] = f"{cfg.last_name}, {cfg.first_name}"
        else:
            display[pid] = candidate
    return display
