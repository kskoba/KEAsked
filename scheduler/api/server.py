"""
FastAPI server for the physician scheduling system.

Launched as a subprocess by the Electron main process.
Listens on 127.0.0.1:5000.

Usage (standalone):
    python -m scheduler.api.server
"""

from __future__ import annotations

import asyncio
import calendar
import datetime
import io
import math
import re
import tempfile
import traceback
from pathlib import Path
from typing import Any

import yaml
import uvicorn
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from scheduler.api.schemas import (
    AssignmentSchema,
    CandidateSchema,
    CandidatesResponse,
    DetectFlatResponse,
    GenerateCachedRequest,
    GenerateRequest,
    ImportFlatRequest,
    ImportRequest,
    ManualAssignRequest,
    ManualAssignResponse,
    PhysicianImportResult,
    AssignOnCallRequest,
    OnCallAssignmentSchema,
    OnCallCandidateSchema,
    OnCallCandidatesResponse,
    PhysicianInfo,
    PhysiciansResponse,
    PhysicianDetail,
    PhysicianDetailsResponse,
    PhysicianUpdateRequest,
    CreatePhysicianRequest,
    RemovePhysicianRequest,
    RemovePhysicianResponse,
    ImportDirectoryResponse,
    LoadScheduleRequest,
    OverrideAllRequest,
    OverrideLogItem,
    OverrideLogResponse,
    OverrideRequest,
    PhysicianRequestedSchema,
    ScheduleResponse,
    ScheduleStatsSchema,
    ShiftSchema,
    UnfilledSlotSchema,
    ValidationIssueSchema,
    ValidationSummaryItem,
    ValidationSummaryResponse,
    ViolationSchema,
    ByteBlocPhysicianSummary,
    ByteBlocPreviewResponse,
    ByteBlocSendRequest,
    ByteBlocSendResponse,
    SequencingRuleSummary,
    SequencingRulesResponse,
    EmailStatusResponse,
    SendReminderEmailRequest,
    SendReminderEmailResponse,
    SkedStatusResponse,
    PeriodInfo,
    PeriodsResponse,
    PhysicianLinkRequest,
    PhysicianLinkResponse,
    SendMonthlyRequestsRequest,
    SendMonthlyRequestsResult,
    SendMonthlyRequestsResponse,
    SurveyInfo,
    SurveysResponse,
    SurveyCompletionRow,
    SurveyCompletionResponse,
    ResendSurveyLinkRequest,
    ResendSurveyLinkResponse,
    NotSubmittedRow,
    SkedImportRequest,
    ResendMonthlyRequestRequest,
    ResendMonthlyRequestResponse,
)
import os

from scheduler.backend import bytebloc as bytebloc_mod
from scheduler.backend import email_sender
from scheduler.backend import sked_client
from scheduler.backend.config import (
    CALL_LINKAGE_VALUES,
    GROUP_B_PREFS,
    VALID_RULE_OVERRIDES,
    VALID_SITES,
    WEEKDAY_VALUES,
    PhysicianConfig,
    add_physician,
    describe_physician_facing_rules,
    load_roster,
    remove_physician,
    save_physician,
)
from scheduler.backend.generator import (
    Assignment,
    OnCallAssignment,
    ScheduleGenerator,
    ScheduleResult,
    ScheduleStats,
    UnfilledSlot,
    _HARD_VIOLATION_RULES,
    generate_schedule,
)
try:
    from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
    _CPSAT_AVAILABLE = True
except Exception:  # pragma: no cover
    _CPSAT_AVAILABLE = False
from scheduler.backend.importer import import_directory, import_single_file
from scheduler.backend.importer_flat import import_flat_file
from scheduler.backend.models import DayAvailability, PhysicianSubmission, ValidationIssue
from scheduler.backend.shifts import ALL_SHIFT_CODES, BLOCKS, SHIFT_TO_BLOCK, Shift


def _display_name(cfg) -> str:
    """
    Full name for anywhere a physician is shown/addressed (sked, emails,
    the Survey Responses viewer) -- prefers first_name + last_name over the
    raw `name` field, which is often just a surname (a historical artifact)
    even when first_name IS on file. Mirrors RosterEditor.jsx's
    displayName() on the frontend. Never touches cfg.name itself, which
    must stay exactly as recorded (it's matched against Excel cell A1 and
    used as the physician_resolver.py lookup key) -- this is display only.
    """
    full = f"{cfg.first_name or ''} {cfg.last_name or ''}".strip()
    return full or cfg.name or cfg.id


def _synthetic_submissions(result: ScheduleResult, roster: dict) -> list[PhysicianSubmission]:
    """
    Build minimal PhysicianSubmission objects from a loaded schedule result so
    that _require_generator() can reconstruct the generator without real
    preference data.  Every physician in the roster is marked available for
    every day of the schedule month — constraint checks will still run and
    produce warnings, but manual assignments won't be blocked by missing subs.
    """
    year, month = result.year, result.month
    num_days = calendar.monthrange(year, month)[1]
    all_blocks = frozenset(range(5))   # blocks 0-4

    # Count existing assigned shifts per physician from the result
    shift_counts: dict[str, int] = {}
    for a in result.assignments:
        shift_counts[a.physician_id] = shift_counts.get(a.physician_id, 0) + 1

    # All physician IDs — from roster plus any in the schedule not in roster
    pids_in_result = {a.physician_id for a in result.assignments}
    all_pids = set(roster.keys()) | pids_in_result

    subs = []
    for pid in all_pids:
        info = roster.get(pid)
        name = getattr(info, 'name', pid) if info is not None else pid
        n = shift_counts.get(pid, 0)
        days = [
            DayAvailability(
                date=datetime.date(year, month, d),
                wants_to_work=True,
                available_blocks=all_blocks,
            )
            for d in range(1, num_days + 1)
        ]
        subs.append(PhysicianSubmission(
            physician_id=pid,
            physician_name=name,
            year=year,
            month=month,
            shifts_requested=n,
            shifts_min=0,
            shifts_max=n + 2,
            days=days,
        ))
    return subs


def _v(v) -> ViolationSchema:
    """Convert a ViolationReason to ViolationSchema, including is_hard flag."""
    return ViolationSchema(
        rule=v.rule,
        description=v.description,
        is_hard=v.rule in _HARD_VIOLATION_RULES,
    )


def _resolve_pid(gen, physician_id: str) -> str:
    """
    Return the canonical physician_id key used in gen.submissions.

    Tries in order:
      1. Exact match on submission keys
      2. Case-insensitive match on submission keys
      3. Case-insensitive match on physician_name inside submissions
      4. Cross-reference via current schedule: find physician_name for this id
         in the current result, then match that name against submission names.
         Handles cases where per-xlsx ids (e.g. "AYeung") differ from
         flat-file or synthetic submission keys (e.g. "Alex Yeung").

    Raises HTTPException(400) if no match is found.
    """
    if physician_id in gen.submissions:
        return physician_id
    lower = physician_id.lower()
    # Case-insensitive ID lookup
    for key in gen.submissions:
        if key.lower() == lower:
            return key
    # Name-based lookup (handles xlsx display names that became physician_ids)
    for key, sub in gen.submissions.items():
        if sub.physician_name.lower() == lower:
            return key
    # Cross-reference via current schedule result:
    # find the physician_name stored in existing assignments for this id,
    # then match that display name against submission physician_names.
    result = _state.get("result")
    if result:
        pid_name: str | None = None
        for a in result.assignments:
            if a.physician_id.lower() == lower:
                pid_name = a.physician_name
                break
        if pid_name:
            pid_name_lower = pid_name.lower()
            for key, sub in gen.submissions.items():
                if sub.physician_name.lower() == pid_name_lower:
                    return key
    raise HTTPException(
        status_code=400,
        detail=f"Physician {physician_id!r} is not in current submissions.",
    )


from scheduler.backend.physician_resolver import (
    build_alias_index,
    build_display_names,
    resolve_physician_id,
    sort_key as _physician_sort_key,
)
from scheduler.backend.validator import day_block_threshold, validate

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Physician Scheduler API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],    # Electron renderer connects from file:// or localhost
    allow_methods=["*"],
    allow_headers=["*"],
)

import os as _os
import sys as _sys

if _os.environ.get("CONFIG_DIR"):
    _CONFIG_DIR = Path(_os.environ["CONFIG_DIR"])
elif getattr(_sys, "frozen", False):
    # PyInstaller bundle — config lives next to the executable
    _CONFIG_DIR = Path(_sys.executable).parent / "config"
else:
    _CONFIG_DIR = Path(__file__).parent.parent / "config"

_SCHEDULER_CONFIG_PATH = _CONFIG_DIR / "scheduler_config.yaml"

# ---------------------------------------------------------------------------
# In-memory state (single-user desktop app — no database needed)
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {
    "submissions": [],
    "roster": {},
    "scheduler_config": {},
    "generator": None,
    "result": None,
    "year": None,
    "month": None,
    "directory": None,
    "source_file": None,
    "progress": {"current": 0, "total": 0, "running": False, "best_unfilled": None},
    "cancel_requested": False,
    # physician_id -> set of rule ids the user has manually overridden this
    # session (see /api/override*). Reset on every fresh import.
    "overrides": {},
}


# Common boilerplate in this practice's submission filenames (template
# name, month, "with 0600 and night Q", revision markers) — stripped
# before trying the filename as an identity candidate, so what's left is
# just whatever a person actually typed as their own name in it.
_FILENAME_BOILERPLATE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december"
    r"|20\d\d|master|preferences?|preference|with|and|night|copy|the|of|for)\b"
    r"|0600|1200|\bq\b|\(\d+\)|[-_()]",
    re.IGNORECASE,
)


def _clean_filename_candidate(sub: PhysicianSubmission) -> str:
    stem = Path(sub.source_file).stem if sub.source_file else sub.physician_id
    cleaned = _FILENAME_BOILERPLATE.sub(" ", stem)
    cleaned = " ".join(cleaned.split())
    # Trailing revision marker (a resubmission renamed "Taylor-1", "Taylor 2",
    # "Taylor(1)", etc. — see _revision_score) isn't part of the physician's
    # name; strip it so "Taylor-1" resolves the same as "Taylor" instead of
    # ending up unresolved. Deliberately narrow (1-2 digits, at the very end,
    # after a separator) so it can't eat a real 4-digit year or a digit that's
    # actually part of someone's name.
    cleaned = re.sub(r"[\s\-_]\(?\d{1,2}\)?$", "", cleaned).strip()
    return cleaned


def _resolve_submission_id(sub: PhysicianSubmission, index: dict) -> str | None:
    """
    Resolve one submission's identity against the alias index, trying
    progressively less-reliable signals in order and stopping at the first
    exact match (resolve_physician_id never fuzzy-matches, so trying more
    candidate strings never risks a wrong guess — it just gives a real,
    human-entered name more chances to be found):

      1. physician_name (the cell the physician was meant to type their
         name into).
      2. raw_name_candidates (per-xlsx only) — other non-empty cells in
         row 1, for when someone typed their name in the wrong spot
         instead of leaving A1 blank/placeholder.
      3. physician_id as-is — for directory imports this is the raw
         filename stem.
      4. physician_id with common filename boilerplate stripped (month
         names, "Master Preferences", "with 0600 and night Q", revision
         markers like "(1)") — so e.g. "October 2026 Brian Whiteside.xlsx"
         resolves via the remaining "Brian Whiteside".

    Row-1 content (1-2) is tried before any filename-derived candidate
    (3-4) deliberately: a filename can be ambiguous in ways a physician's
    own typed name isn't (e.g. a bare "LAM" in a filename could mean
    either Lam physician, but row 1 saying "Kenneth Lam" is unambiguous).
    """
    for candidate in (sub.physician_name, *sub.raw_name_candidates):
        resolved = resolve_physician_id(candidate, index)
        if resolved:
            return resolved
    return (
        resolve_physician_id(sub.physician_id, index)
        or resolve_physician_id(_clean_filename_candidate(sub), index)
    )


def _revision_score(sub: PhysicianSubmission) -> int:
    """
    Heuristic for picking the authoritative file when the same physician
    has multiple submissions this month (a resubmitted/revised request).
    Higher wins. "updated" anywhere in the filename is a strong deliberate
    signal; a trailing revision number — "(N)" (e.g. from a browser
    appending a number to a duplicate download) or a bare "-N"/" N" (this
    practice's own resubmission convention, e.g. "Taylor-1.xlsx" for
    Michael Taylor's 2nd version) — is a weaker but still meaningful
    "later" signal — N itself is used so 2/(2) beats 1/(1) beats no marker.
    The bare form is deliberately narrow (1-2 digits, right before the
    extension, after a separator) so it can't mistake a 4-digit year for a
    revision number.
    """
    name = Path(sub.source_file).name.lower()
    score = 1000 if "updated" in name else 0
    m = re.search(r"\((\d+)\)", name) or re.search(r"[\s\-_](\d{1,2})\.\w+$", name)
    if m:
        score += int(m.group(1))
    return score


def _apply_roster(submissions: list[PhysicianSubmission], roster: dict) -> set[str]:
    """
    Resolve each submission's physician_id to the roster's canonical id
    (matching against id/name/aliases — see physician_resolver.py) and merge
    in per-physician config (rule overrides etc.). Rewrites physician_id and
    physician_name in place to the canonical values so every submission that
    resolves to the same physician — regardless of which name variant the
    source file used — ends up identified consistently.

    Also deduplicates: when a directory import contains multiple files that
    resolve to the same physician (a resubmission), only the highest-scoring
    one per _revision_score is kept — mutates `submissions` in place to drop
    the others, so neither the Validate page nor the generator ever sees a
    physician listed/counted twice.

    physician_name is set to the same "Lastname, F" display form used on
    the Validate page (see physician_resolver.build_display_names), not
    the roster's raw `name` field — this is the single place that name
    reaches every submission, so the generated schedule (grid, sidebar,
    export) ends up using the same names the Validate page showed, instead
    of drifting to whatever the roster's full-name field happens to say.

    Returns the set of physician_id values (as originally seen, unmodified)
    that could not be resolved against the roster at all — these need a
    human to either fix the source data or add an alias to physicians.yaml.
    """
    index = build_alias_index(roster)
    display_names = build_display_names(roster)
    unresolved: set[str] = set()
    # canonical_id -> that physician's index in `ordered`, so a later
    # higher-scoring duplicate can replace it in place (by index, not by
    # equality — PhysicianSubmission's dataclass __eq__ is field-wise, so
    # two genuinely-identical duplicate files would break a `list.index()`
    # based approach).
    best_index: dict[str, int] = {}
    ordered: list[PhysicianSubmission] = []
    for sub in submissions:
        canonical_id = _resolve_submission_id(sub, index)
        if canonical_id is None:
            unresolved.add(sub.physician_id)
            ordered.append(sub)
            continue
        cfg = roster[canonical_id]
        sub.physician_id = cfg.id
        sub.physician_name = display_names.get(cfg.id, cfg.name)
        sub.rule_overrides = dict(cfg.rule_overrides)
        if canonical_id not in best_index:
            best_index[canonical_id] = len(ordered)
            ordered.append(sub)
        else:
            idx = best_index[canonical_id]
            if _revision_score(sub) >= _revision_score(ordered[idx]):
                ordered[idx] = sub
            # else: sub is a lower-scoring duplicate — silently dropped
    submissions[:] = ordered
    return unresolved


def _build_import_results(
    submissions: list[PhysicianSubmission],
    unresolved: set[str] | None = None,
    roster: dict | None = None,
) -> list[PhysicianImportResult]:
    """
    Build one PhysicianImportResult per submission for the Validate page.

    Display name is "Lastname, F" (full first name only where needed to
    disambiguate — see physician_resolver.build_display_names), and the
    list is sorted by last name, with unresolved-identity submissions
    surfaced first since those need attention before anything else.
    """
    unresolved = unresolved or set()
    roster = roster or {}
    display_names = build_display_names(roster)
    paired: list[tuple[PhysicianSubmission, PhysicianImportResult]] = []
    for sub in submissions:
        vr = validate(sub)
        if sub.physician_id in unresolved:
            vr.issues.insert(0, ValidationIssue(
                severity="error",
                rule="unresolved_physician",
                message=(
                    f"{sub.physician_id!r} does not match any physician in the "
                    f"roster. Add it as an alias in physicians.yaml if this is a "
                    f"known physician under a different name."
                ),
                physician_id=sub.physician_id,
            ))
        min_blocks = day_block_threshold(sub)
        valid_days = sum(1 for d in sub.days if d.is_valid_day(min_blocks))
        valid_blocks = sum(len(d.available_blocks) for d in sub.days if d.is_valid_day(min_blocks))
        valid_weekends = sum(1 for d in sub.days if d.is_valid_weekend(min_blocks))
        anchored = sum(1 for d in sub.days if d.is_anchored(min_blocks))
        paired.append((sub, PhysicianImportResult(
            physician_id=sub.physician_id,
            physician_name=display_names.get(sub.physician_id, sub.physician_name),
            shifts_requested=sub.shifts_requested,
            shifts_min=sub.shifts_min,
            shifts_max=sub.shifts_max,
            shifts_2400h_requested=sub.shifts_2400h_requested,
            shifts_0600h_requested=sub.shifts_0600h_requested,
            valid_days=valid_days,
            valid_blocks=valid_blocks,
            valid_weekend_days=valid_weekends,
            anchored_days=anchored,
            issues=(issues_schema := [
                ValidationIssueSchema(
                    severity=i.severity,
                    rule=i.rule,
                    message=i.message,
                    physician_id=i.physician_id or sub.physician_id,
                    overridden=i.rule in _state["overrides"].get(sub.physician_id, set()),
                )
                for i in vr.issues
            ]),
            is_valid=not any(isch.severity == "error" and not isch.overridden for isch in issues_schema),
        )))

    def _order(pair: tuple[PhysicianSubmission, PhysicianImportResult]) -> tuple:
        sub, _ = pair
        if sub.physician_id in unresolved:
            return (0, sub.physician_name.casefold())
        cfg = roster.get(sub.physician_id)
        key = _physician_sort_key(cfg) if cfg else (sub.physician_name.casefold(), "")
        return (1, key)

    paired.sort(key=_order)
    return [result for _, result in paired]


def _load_scheduler_config() -> dict:
    import os
    with _SCHEDULER_CONFIG_PATH.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return cfg


def _push_physician_rules_summary(physician_id: str, cfg: PhysicianConfig, roster: dict) -> None:
    """
    Auto-sync: push this physician's plain-language "what's actually
    configured for you" summary to sked on every roster save (see
    update_physician/create_physician below), for display in sked's "My
    Rules" view. Combines physicians.yaml fields (describe_physician_facing_rules)
    with any scheduler_config.yaml person-specific pair/sequencing rule
    that names them.

    Never raises -- sked.yaml missing, sked unreachable, or this physician
    not yet having a sked account are all just skipped/logged, never
    surfaced to the caller. This sync is a nice-to-have that keeps sked in
    sync automatically; it must never block saving a physician's config
    locally, which stays the source of truth regardless of whether this
    push succeeds.
    """
    sked_config = sked_client.load_sked_config()
    if sked_config is None or not sked_client.is_fully_configured(sked_config):
        return
    try:
        items = describe_physician_facing_rules(cfg)
        scheduler_cfg = _load_scheduler_config()
        for rule in build_sequencing_rule_summaries(scheduler_cfg, roster):
            if physician_id in rule.physician_ids:
                items.append(rule.description)
        sked_client.push_physician_rules(sked_config, physician_id, items)
    except Exception as exc:
        print(f"[sked sync] WARNING: could not push rules summary for {physician_id!r}: {exc}")


def _display_name_or_id(pid: str, display_names: dict[str, str]) -> str:
    return display_names.get(pid, pid)


def build_sequencing_rule_summaries(cfg: dict, roster: dict) -> list[SequencingRuleSummary]:
    """
    Turn scheduler_config.yaml's person-specific pair/sequencing rules into
    plain-language summaries for a read-only display (roster editor's
    "Scheduling Rules" panel). Nothing here is ever written back -- this is
    strictly a renderer for what's already in the yaml file; edit that file
    directly (or ask support) to actually change a rule.
    """
    display_names = build_display_names(roster)
    summaries: list[SequencingRuleSummary] = []

    def names(ids: list[str]) -> list[str]:
        return [_display_name_or_id(p, display_names) for p in ids]

    for rule in cfg.get("timed_separation", []) or []:
        phys = rule.get("physicians", [])
        if len(phys) != 2:
            continue
        a, b = names(phys)
        parts = [f"If scheduled the same day (or adjacent days), their shift start times must be at least {rule.get('min_hours_gap', 0)} hours apart."]
        forbidden = rule.get("forbidden_time_pairs") or []
        if forbidden:
            pairs_text = ", ".join(f"{p[0]}/{p[1]}" for p in forbidden)
            parts.append(f"These specific start-time combinations are never allowed together regardless of gap: {pairs_text}.")
        summaries.append(SequencingRuleSummary(
            kind="timed_separation", physician_ids=phys, physician_names=[a, b],
            description=f"{a} and {b}: " + " ".join(parts),
        ))

    for rule in cfg.get("conditional_cowork", []) or []:
        phys = rule.get("physicians", [])
        if len(phys) != 2:
            continue
        a, b = names(phys)
        parts = []
        if rule.get("no_shared_weekends"):
            parts.append("They can never both have a shift on the same weekend (Fri/Sat/Sun) date.")
        required_time = rule.get("weekday_requires_one_at")
        if required_time:
            parts.append(f"On a weekday where both are scheduled, exactly one of them must be on the {required_time} shift.")
        summaries.append(SequencingRuleSummary(
            kind="conditional_cowork", physician_ids=phys, physician_names=[a, b],
            description=f"{a} and {b}: " + " ".join(parts),
        ))

    for rule in cfg.get("forbidden_precursor_shifts", []) or []:
        pid = rule.get("physician")
        if not pid:
            continue
        forbidden = ", ".join(rule.get("forbidden_precursor_times", []))
        target = rule.get("target_time")
        summaries.append(SequencingRuleSummary(
            kind="forbidden_precursor_shifts", physician_ids=[pid], physician_names=names([pid]),
            description=(
                f"{_display_name_or_id(pid, display_names)}: a {forbidden} shift may never be the day "
                f"immediately before a {target} shift — only an earlier shift, or a day off, may precede it."
            ),
        ))

    for rule in cfg.get("night_chain_ramp_in", []) or []:
        pid = rule.get("physician")
        if not pid:
            continue
        preferred = rule.get("preferred_precursor_time")
        summaries.append(SequencingRuleSummary(
            kind="night_chain_ramp_in", physician_ids=[pid], physician_names=names([pid]),
            description=(
                f"{_display_name_or_id(pid, display_names)}: prefers a shift the day before starting a new "
                f"run of 2400h night shifts, rather than coming off a day off"
                + (f" — ideally a {preferred} shift." if preferred else ".")
            ),
        ))

    for rule in cfg.get("linked_rest_pairs", []) or []:
        phys = rule.get("physicians", [])
        if len(phys) != 2:
            continue
        a, b = names(phys)
        inactive_note = ""
        for p in phys:
            c = roster.get(p)
            if c and not c.active:
                inactive_note = f" ({_display_name_or_id(p, display_names)} is currently inactive — this rule has no effect.)"
        summaries.append(SequencingRuleSummary(
            kind="linked_rest_pairs", physician_ids=phys, physician_names=[a, b],
            description=(
                f"{a} and {b}: treated as one combined timeline for rest purposes — never both scheduled the "
                f"same day, and the same rest-gap/consecutive-run rules that apply to one person's own schedule "
                f"apply across their combined schedule too.{inactive_note}"
            ),
        ))

    return summaries


@app.get("/api/scheduling-rules/person-specific", response_model=SequencingRulesResponse)
def get_person_specific_scheduling_rules() -> SequencingRulesResponse:
    """
    Read-only: every person-specific pair/sequencing rule currently in
    scheduler_config.yaml, rendered in plain language. Never modifies
    anything — the roster editor's "Scheduling Rules" panel uses this to
    show what's configured, with a note to contact support for changes.
    """
    cfg = _load_scheduler_config()
    roster = load_roster()
    return SequencingRulesResponse(rules=build_sequencing_rule_summaries(cfg, roster))


def _shift_to_schema(shift: Shift) -> ShiftSchema:
    return ShiftSchema(
        time=shift.time,
        site=shift.site,
        code=shift.code,
        site_group=shift.site_group.value,
    )


def _result_to_response(result: ScheduleResult) -> ScheduleResponse:
    submissions: list[PhysicianSubmission] = _state.get("submissions") or []
    requested = {
        sub.physician_id: PhysicianRequestedSchema(
            shifts_requested=sub.shifts_requested,
            shifts_max=sub.shifts_max,
            shifts_2400h_requested=sub.shifts_2400h_requested,
            shifts_0600h_requested=sub.shifts_0600h_requested,
        )
        for sub in submissions
    }
    return ScheduleResponse(
        year=result.year,
        month=result.month,
        assignments=[
            AssignmentSchema(
                date=a.date.isoformat(),
                shift=_shift_to_schema(a.shift),
                physician_id=a.physician_id,
                physician_name=a.physician_name,
                is_manual=a.is_manual,
            )
            for a in result.assignments
        ],
        unfilled=[
            UnfilledSlotSchema(
                date=u.date.isoformat(),
                shift=_shift_to_schema(u.shift),
                candidates=[
                    CandidateSchema(
                        physician_id=c.physician_id,
                        physician_name=c.physician_name,
                        violations=[
                            _v(v)
                            for v in c.violations
                        ],
                        is_hard_blocked=c.is_hard_blocked,
                    )
                    for c in u.candidates
                ],
            )
            for u in result.unfilled
        ],
        issues=result.issues,
        stats=ScheduleStatsSchema(**result.stats.__dict__) if result.stats else None,
        on_calls=[
            OnCallAssignmentSchema(
                date=oc.date.isoformat(),
                call_type=oc.call_type,
                physician_id=oc.physician_id,
                physician_name=oc.physician_name,
            )
            for oc in result.on_calls
        ],
        requested=requested,
    )


# ---------------------------------------------------------------------------
# Generator rebuild helper
# ---------------------------------------------------------------------------

def _require_generator() -> tuple:
    """
    Return (gen, result) from _state, rebuilding gen from cached submissions
    if it was lost (e.g. after loading a schedule from file or server restart).

    Raises HTTPException(400) if result or submissions are absent.
    """
    result: ScheduleResult | None = _state.get("result")
    if result is None:
        raise HTTPException(status_code=400, detail="No schedule in memory.")

    gen: ScheduleGenerator | None = _state.get("generator")
    if gen is not None:
        return gen, result

    # Generator absent — try to rebuild from cached submissions.
    submissions: list[PhysicianSubmission] = _state.get("submissions") or []
    if not submissions:
        raise HTTPException(
            status_code=400,
            detail="No schedule in memory. Re-import physician preferences to enable this action.",
        )
    roster = _state.get("roster") or {}
    cfg = _state.get("scheduler_config") or {}
    if _CPSAT_AVAILABLE:
        gen = CpsatScheduleGenerator(submissions, roster, cfg)
    else:
        gen = ScheduleGenerator(submissions, roster, cfg)
    for a in result.assignments:
        gen._assign(a.physician_id, a.date, a.shift)
    _state["generator"] = gen   # cache for subsequent calls
    return gen, result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/detect-flat", response_model=DetectFlatResponse)
def detect_flat(file: str) -> DetectFlatResponse:
    """
    Read the first data row of a flat file and return the year/month found.
    Used by the frontend to auto-fill the month/year selectors.
    """
    import openpyxl
    fp = Path(file)
    if not fp.is_file():
        raise HTTPException(status_code=400, detail=f"File not found: {file}")
    try:
        from collections import Counter
        from scheduler.backend.importer_flat import _to_date
        wb = openpyxl.load_workbook(fp, data_only=True, read_only=True)
        ws = wb.active
        counts: Counter = Counter()
        for row in ws.iter_rows(min_row=2, max_row=200, values_only=True):
            if not row[0] or not row[1]:
                continue
            d = _to_date(row[1])
            if d:
                counts[(d.year, d.month)] += 1
        wb.close()
        if counts:
            (year, month), _ = counts.most_common(1)[0]
            return DetectFlatResponse(year=year, month=month)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    raise HTTPException(status_code=422, detail="Could not detect year/month from file.")


@app.get("/api/physicians", response_model=PhysiciansResponse)
def get_physicians() -> PhysiciansResponse:
    """Return all physicians from the roster."""
    roster = load_roster()
    return PhysiciansResponse(
        physicians=[
            PhysicianInfo(
                id=cfg.id,
                name=cfg.name,
                active=cfg.active,
                max_consecutive_shifts=cfg.max_consecutive_shifts,
                group_b_site_preference=cfg.group_b_site_preference,
                forbidden_sites=cfg.forbidden_sites,
            )
            for cfg in roster.values()
        ]
    )


def _physician_to_detail(cfg: PhysicianConfig) -> PhysicianDetail:
    return PhysicianDetail(
        id=cfg.id,
        name=cfg.name,
        email=cfg.email,
        notes=cfg.notes,
        active=cfg.active,
        last_name=cfg.last_name,
        first_name=cfg.first_name,
        aliases=list(cfg.aliases),
        max_consecutive_shifts=cfg.max_consecutive_shifts,
        max_consecutive_nights=cfg.max_consecutive_nights,
        typical_shifts_per_month=cfg.typical_shifts_per_month,
        typical_0600h_per_month=cfg.typical_0600h_per_month,
        typical_2400h_per_month=cfg.typical_2400h_per_month,
        group_b_site_preference=cfg.group_b_site_preference,
        forbidden_sites=list(cfg.forbidden_sites),
        only_2400h=cfg.only_2400h,
        only_0600h=cfg.only_0600h,
        post_block_rest_days=cfg.post_block_rest_days,
        post_block_min_length=cfg.post_block_min_length,
        call_linkage=cfg.call_linkage,
        max_consecutive_same_site=cfg.max_consecutive_same_site,
        avoid_weekday=cfg.avoid_weekday,
        prefer_weekend_clumping=cfg.prefer_weekend_clumping,
        prefer_weekends=cfg.prefer_weekends,
        max_weekends=cfg.max_weekends,
        honor_all_requests=cfg.honor_all_requests,
        prefer_singleton_nights=cfg.prefer_singleton_nights,
        prefer_clustered_nights=cfg.prefer_clustered_nights,
        forbidden_shift_times=list(cfg.forbidden_shift_times),
        no_call=cfg.no_call,
        avoid_mondays=cfg.avoid_mondays,
        rest_after_late_shift=cfg.rest_after_late_shift,
        max_consecutive_1800h=cfg.max_consecutive_1800h,
        cap_at_requested=cfg.cap_at_requested,
        special_provisions=cfg.special_provisions,
        casual=cfg.casual,
        rule_overrides=dict(cfg.rule_overrides),
    )


@app.get("/api/physicians/full", response_model=PhysicianDetailsResponse)
def get_physicians_full() -> PhysicianDetailsResponse:
    """
    Every field of every physician in the roster — for the roster editor
    window, which needs more than the summary /api/physicians exposes.
    """
    roster = load_roster()
    physicians = sorted(roster.values(), key=lambda c: (c.last_name or c.name, c.first_name))
    return PhysicianDetailsResponse(physicians=[_physician_to_detail(cfg) for cfg in physicians])


@app.put("/api/physicians/{physician_id}", response_model=PhysicianDetail)
def update_physician(physician_id: str, body: PhysicianUpdateRequest) -> PhysicianDetail:
    """
    Save edits to one existing physician back into physicians.yaml.

    Editing only — physician_id must already exist in the roster (roster
    editor doesn't support adding/removing physicians). Values are
    validated the same way the file itself is validated on load (valid
    group_b_site_preference, valid forbidden site names, known
    rule_overrides keys) before anything is written.
    """
    roster = load_roster()
    if physician_id not in roster:
        raise HTTPException(status_code=404, detail=f"No physician with id {physician_id!r}.")
    if body.id != physician_id:
        raise HTTPException(status_code=400, detail="Body id must match the URL id.")

    if body.group_b_site_preference and body.group_b_site_preference not in GROUP_B_PREFS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid group_b_site_preference {body.group_b_site_preference!r}. "
                   f"Valid values: {sorted(GROUP_B_PREFS)}",
        )
    unknown_sites = set(body.forbidden_sites) - VALID_SITES
    if unknown_sites:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown forbidden site(s): {sorted(unknown_sites)}. Valid sites: {sorted(VALID_SITES)}",
        )
    unknown_rules = set(body.rule_overrides) - VALID_RULE_OVERRIDES
    if unknown_rules:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown rule_overrides key(s): {sorted(unknown_rules)}. "
                   f"Valid keys: {sorted(VALID_RULE_OVERRIDES)}",
        )
    if body.call_linkage and body.call_linkage not in CALL_LINKAGE_VALUES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid call_linkage {body.call_linkage!r}. Valid values: {sorted(CALL_LINKAGE_VALUES)}",
        )
    if body.avoid_weekday and body.avoid_weekday not in WEEKDAY_VALUES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid avoid_weekday {body.avoid_weekday!r}. Valid values: {sorted(WEEKDAY_VALUES)}",
        )

    # default_shifts_requested / combined_headcount / priority_weight /
    # float_shift_target aren't exposed in the roster editor's form, so they
    # must be carried forward from the existing config rather than left to
    # default away — otherwise any unrelated edit through the UI silently
    # wipes them (confirmed: this is exactly what happened to MacGougan's
    # default_shifts_requested).
    existing = roster[physician_id]

    cfg = PhysicianConfig(
        id=physician_id,
        name=body.name,
        email=body.email,
        notes=body.notes,
        active=body.active,
        last_name=body.last_name,
        first_name=body.first_name,
        aliases=list(body.aliases),
        max_consecutive_shifts=body.max_consecutive_shifts,
        max_consecutive_nights=body.max_consecutive_nights,
        typical_shifts_per_month=body.typical_shifts_per_month,
        typical_0600h_per_month=body.typical_0600h_per_month,
        typical_2400h_per_month=body.typical_2400h_per_month,
        group_b_site_preference=body.group_b_site_preference,
        forbidden_sites=list(body.forbidden_sites),
        only_2400h=body.only_2400h,
        only_0600h=body.only_0600h,
        post_block_rest_days=body.post_block_rest_days,
        post_block_min_length=body.post_block_min_length,
        call_linkage=body.call_linkage,
        max_consecutive_same_site=body.max_consecutive_same_site,
        avoid_weekday=body.avoid_weekday,
        prefer_weekend_clumping=body.prefer_weekend_clumping,
        prefer_weekends=body.prefer_weekends,
        max_weekends=body.max_weekends,
        honor_all_requests=body.honor_all_requests,
        prefer_singleton_nights=body.prefer_singleton_nights,
        prefer_clustered_nights=body.prefer_clustered_nights,
        forbidden_shift_times=list(body.forbidden_shift_times),
        no_call=body.no_call,
        avoid_mondays=body.avoid_mondays,
        rest_after_late_shift=body.rest_after_late_shift,
        # No longer an independent roster-editor field -- 1800h-in-a-row
        # always tracks max_consecutive_shifts (SIAR) now, per 2026-09-29
        # annual survey import. body.max_consecutive_1800h is stale/unedited
        # form state at this point (the input was removed from the UI), so
        # it's never read here.
        max_consecutive_1800h=body.max_consecutive_shifts,
        cap_at_requested=body.cap_at_requested,
        special_provisions=body.special_provisions,
        casual=body.casual,
        rule_overrides=dict(body.rule_overrides),
        default_shifts_requested=existing.default_shifts_requested,
        combined_headcount=existing.combined_headcount,
        priority_weight=existing.priority_weight,
        anchor_preference=existing.anchor_preference,
        float_shift_target=existing.float_shift_target,
    )

    try:
        save_physician(physician_id, cfg)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Keep an already-imported session's cached roster in sync so the
    # change is reflected immediately without a re-import.
    if physician_id in _state.get("roster", {}):
        _state["roster"][physician_id] = cfg

    roster[physician_id] = cfg
    _push_physician_rules_summary(physician_id, cfg, roster)

    return _physician_to_detail(cfg)


# The exact phrase a human must type before delete_physician() will run.
# Checked case-sensitively, verbatim — see bytebloc.py's identical pattern
# for send_shift_requests(). Do not make this configurable.
REMOVE_CONFIRMATION_PHRASE = "REMOVE"


@app.post("/api/physicians", response_model=PhysicianDetail)
def create_physician(body: CreatePhysicianRequest) -> PhysicianDetail:
    """
    Add a new physician to the roster. id must be a single word of
    letters/numbers not already in use; every other field starts at the
    same bare defaults as any minimally-configured roster entry (active,
    3 max consecutive shifts, no site preference or overrides) — edit
    further from the roster editor's detail form afterward.
    """
    new_id = body.id.strip()
    first_name = body.first_name.strip()
    last_name = body.last_name.strip()

    if not new_id or not re.fullmatch(r"[A-Za-z0-9]+", new_id):
        raise HTTPException(status_code=422, detail="Id must be a single word of letters/numbers only.")
    if not first_name or not last_name:
        raise HTTPException(status_code=422, detail="First and last name are both required.")

    roster = load_roster()
    if new_id in roster:
        raise HTTPException(status_code=409, detail=f"A physician with id {new_id!r} already exists.")

    cfg = PhysicianConfig(
        id=new_id,
        name=f"{first_name} {last_name}",
        first_name=first_name,
        last_name=last_name,
        active=True,
    )

    try:
        add_physician(cfg)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if _state.get("roster"):
        _state["roster"][new_id] = cfg

    roster[new_id] = cfg
    _push_physician_rules_summary(new_id, cfg, roster)

    return _physician_to_detail(cfg)


@app.post("/api/physicians/{physician_id}/remove", response_model=RemovePhysicianResponse)
def delete_physician(physician_id: str, body: RemovePhysicianRequest) -> RemovePhysicianResponse:
    """
    Permanently remove a physician from the roster. Requires the literal
    confirmation phrase "REMOVE", typed by a human — this is a hard gate,
    not a formality; the frontend must collect it fresh each time, never
    pre-fill or remember it.
    """
    if body.confirmation != REMOVE_CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f'Type "{REMOVE_CONFIRMATION_PHRASE}" exactly to confirm removal.',
        )

    roster = load_roster()
    if physician_id not in roster:
        raise HTTPException(status_code=404, detail=f"No physician with id {physician_id!r}.")

    try:
        remove_physician(physician_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    _state.get("roster", {}).pop(physician_id, None)

    return RemovePhysicianResponse(ok=True, status=f"Removed {physician_id}.")


def _auto_override_flagged_physicians(submissions: list[PhysicianSubmission], roster: dict) -> None:
    """
    Auto-override every current error for physicians flagged special_provisions
    or casual in physicians.yaml — same effect as clicking "Override All" for
    them, done automatically at import time so nobody has to remember to do
    it every month. Errors remain visible in the Validate page's expandable
    detail, just already marked overridden. Must run after _apply_roster
    (needs sub.physician_id already resolved to the roster's canonical id);
    only applies to submissions that actually resolved — an
    unresolved_physician error still needs a human regardless of any flag.
    """
    for sub in submissions:
        cfg = roster.get(sub.physician_id)
        if not cfg or not (cfg.special_provisions or cfg.casual):
            continue
        vr = validate(sub)
        error_rules = {i.rule for i in vr.issues if i.severity == "error"}
        if error_rules:
            _state["overrides"].setdefault(sub.physician_id, set()).update(error_rules)


def _apply_shift_count_overrides(submissions: list[PhysicianSubmission], roster: dict) -> None:
    """
    Apply two per-physician shift-count adjustments from physicians.yaml,
    after _apply_roster has resolved sub.physician_id to the roster's
    canonical id:

    - default_shifts_requested: fallback only -- used as shifts_requested
      when that month's submission doesn't actually state a number
      (shifts_requested == 0, meaning the N cell was blank/unparseable),
      never overriding a real stated number. A physician who reliably
      reports their own count every month should always have that number
      honored, even if this field is still set from an earlier month
      where their reporting genuinely was unreliable -- confirmed as a
      real bug (2026-09-29, MacGougan): this used to override a real,
      correctly-filled-in 10 with a stale default of 9. shifts_max is
      floored to at least the applied value too, so a stale lower max
      can't silently undercut it.
    - combined_headcount: multiply shifts_requested/min/max by this factor
      — for a roster identity shared by more than one real person (each
      submitting their own identical-values sheet under aliases that all
      resolve to the same id), since only one of their submissions
      survives id resolution and its counts reflect just one person's
      share of the combined capacity.
    """
    for sub in submissions:
        cfg = roster.get(sub.physician_id)
        if not cfg:
            continue
        if cfg.default_shifts_requested is not None and sub.shifts_requested == 0:
            sub.shifts_requested = cfg.default_shifts_requested
            if sub.shifts_max < cfg.default_shifts_requested:
                sub.shifts_max = cfg.default_shifts_requested
        if cfg.combined_headcount != 1:
            sub.shifts_requested *= cfg.combined_headcount
            sub.shifts_min *= cfg.combined_headcount
            sub.shifts_max *= cfg.combined_headcount


def _apply_casual_availability_default(submissions: list[PhysicianSubmission], roster: dict) -> None:
    """
    A casual physician who marks days available but never fills in a
    monthly shift-count (shifts_requested == 0) hasn't said "I want zero
    shifts" — they just didn't state a number. Treat their requested count
    as however many days they marked available (wants_to_work), matching
    how the human scheduler has read this in practice (e.g. Felicity Brown
    marked 2 days available with no stated count and was scheduled for
    exactly 2 shifts).

    A casual physician who DOES state a count (e.g. Gill) keeps that value
    as their max, unaffected by this — this only fills in a genuinely
    unstated number. Only applies to casual physicians; a non-casual
    physician's blank submission is left for the existing
    effective_requested fallback in generator_cpsat.py to handle. Does not
    change scheduling priority — casual physicians remain lowest-priority
    regardless of how their requested count was determined.
    """
    for sub in submissions:
        cfg = roster.get(sub.physician_id)
        if not cfg or not cfg.casual or sub.shifts_requested > 0:
            continue
        available_days = sum(1 for d in sub.days if d.wants_to_work)
        if available_days > 0:
            sub.shifts_requested = available_days
            if sub.shifts_max < available_days:
                sub.shifts_max = available_days


@app.post("/api/import", response_model=ImportDirectoryResponse)
def import_submissions(body: ImportRequest) -> ImportDirectoryResponse:
    """Import all .xlsx files from a directory for the given year/month."""
    directory = Path(body.directory)
    if not directory.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory not found: {body.directory}")
    try:
        roster = load_roster()
        scheduler_cfg = _load_scheduler_config()
        submissions = import_directory(directory, body.year, body.month)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    _state["overrides"] = {}
    unresolved = _apply_roster(submissions, roster)
    _auto_override_flagged_physicians(submissions, roster)
    _apply_shift_count_overrides(submissions, roster)
    _apply_casual_availability_default(submissions, roster)
    results = _build_import_results(submissions, unresolved, roster)
    _state.update(submissions=submissions, roster=roster, scheduler_config=scheduler_cfg,
                  year=body.year, month=body.month, directory=body.directory, source_file=None)

    valid_count = sum(1 for r in results if r.is_valid)
    return ImportDirectoryResponse(year=body.year, month=body.month, directory=body.directory,
                                   physicians=results, total_physicians=len(results),
                                   valid_physicians=valid_count)


@app.post("/api/import-flat", response_model=ImportDirectoryResponse)
def import_flat(body: ImportFlatRequest) -> ImportDirectoryResponse:
    """Import a single flat-table .xlsx file (all physicians in one sheet)."""
    file_path = Path(body.file)
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail=f"File not found: {body.file}")
    try:
        roster = load_roster()
        scheduler_cfg = _load_scheduler_config()
        submissions = import_flat_file(file_path, body.year, body.month)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    _state["overrides"] = {}
    unresolved = _apply_roster(submissions, roster)
    _auto_override_flagged_physicians(submissions, roster)
    _apply_shift_count_overrides(submissions, roster)
    _apply_casual_availability_default(submissions, roster)
    results = _build_import_results(submissions, unresolved, roster)
    _state.update(submissions=submissions, roster=roster, scheduler_config=scheduler_cfg,
                  year=body.year, month=body.month, directory=None, source_file=str(file_path))

    valid_count = sum(1 for r in results if r.is_valid)
    return ImportDirectoryResponse(year=body.year, month=body.month,
                                   directory=str(file_path),
                                   physicians=results, total_physicians=len(results),
                                   valid_physicians=valid_count)


def _current_import_response() -> ImportDirectoryResponse:
    """
    Rebuild the Validate-page response from current session state (after an
    override change) without re-reading anything from disk.
    """
    submissions: list[PhysicianSubmission] = _state.get("submissions") or []
    roster = _state.get("roster") or {}
    index = build_alias_index(roster)
    unresolved = {
        sub.physician_id for sub in submissions
        if _resolve_submission_id(sub, index) is None
    }
    results = _build_import_results(submissions, unresolved, roster)
    valid_count = sum(1 for r in results if r.is_valid)
    return ImportDirectoryResponse(
        year=_state.get("year"), month=_state.get("month"),
        directory=_state.get("directory") or _state.get("source_file") or "",
        physicians=results, total_physicians=len(results),
        valid_physicians=valid_count,
    )


@app.post("/api/override", response_model=ImportDirectoryResponse)
def override_issue(body: OverrideRequest) -> ImportDirectoryResponse:
    """Mark one validation issue (physician_id + rule) as overridden for this session."""
    _state["overrides"].setdefault(body.physician_id, set()).add(body.rule)
    return _current_import_response()


@app.post("/api/override-clear", response_model=ImportDirectoryResponse)
def override_clear(body: OverrideRequest) -> ImportDirectoryResponse:
    """Undo a single override."""
    _state["overrides"].get(body.physician_id, set()).discard(body.rule)
    return _current_import_response()


@app.post("/api/override-all", response_model=ImportDirectoryResponse)
def override_all(body: OverrideAllRequest) -> ImportDirectoryResponse:
    """Override every current error-severity issue for one physician."""
    submissions: list[PhysicianSubmission] = _state.get("submissions") or []
    sub = next((s for s in submissions if s.physician_id == body.physician_id), None)
    if sub is None:
        raise HTTPException(status_code=404, detail=f"No submission for physician_id {body.physician_id!r}")
    vr = validate(sub)
    error_rules = {i.rule for i in vr.issues if i.severity == "error"}
    if not error_rules:
        index = build_alias_index(_state.get("roster") or {})
        if _resolve_submission_id(sub, index) is None:
            error_rules = {"unresolved_physician"}
    _state["overrides"].setdefault(body.physician_id, set()).update(error_rules)
    return _current_import_response()


@app.get("/api/validation-summary", response_model=ValidationSummaryResponse)
def validation_summary() -> ValidationSummaryResponse:
    """
    Plain-English error list per physician, excluding any issue that's been
    overridden this session. Meant to be reviewed as a whole before
    generating, or handed to someone else who isn't looking at the app.
    """
    submissions: list[PhysicianSubmission] = _state.get("submissions") or []
    roster = _state.get("roster") or {}
    index = build_alias_index(roster)
    unresolved = {
        sub.physician_id for sub in submissions
        if _resolve_submission_id(sub, index) is None
    }
    results = _build_import_results(submissions, unresolved, roster)
    items = []
    for r in results:
        errors = [i.message for i in r.issues if i.severity == "error" and not i.overridden]
        if errors:
            items.append(ValidationSummaryItem(
                physician_id=r.physician_id, physician_name=r.physician_name, errors=errors,
            ))
    return ValidationSummaryResponse(items=items)


@app.get("/api/override-log", response_model=OverrideLogResponse)
def override_log() -> OverrideLogResponse:
    """
    What's been overridden this session and why — for deciding afterward
    whether any of these should become a permanent rule_override in
    physicians.yaml instead of a one-off click.
    """
    submissions: list[PhysicianSubmission] = _state.get("submissions") or []
    roster = _state.get("roster") or {}
    display_names = build_display_names(roster)
    by_id = {sub.physician_id: sub for sub in submissions}
    items = []
    for physician_id, rules in _state.get("overrides", {}).items():
        if not rules:
            continue
        sub = by_id.get(physician_id)
        name = display_names.get(physician_id, sub.physician_name if sub else physician_id)
        messages_by_rule = {}
        if sub is not None:
            vr = validate(sub)
            messages_by_rule = {i.rule: i.message for i in vr.issues}
        for rule in rules:
            message = messages_by_rule.get(rule, "(no longer applicable — submission has changed)")
            items.append(OverrideLogItem(physician_name=name, rule=rule, message=message))
    return OverrideLogResponse(items=items)


@app.get("/api/email/status", response_model=EmailStatusResponse)
def email_status() -> EmailStatusResponse:
    """Whether outgoing email (reminder notifications) is configured."""
    config = email_sender.load_email_config()
    configured = config is not None and email_sender.is_fully_configured(config)
    return EmailStatusResponse(configured=configured)


@app.post("/api/email/send-reminder", response_model=SendReminderEmailResponse)
def send_reminder_email(body: SendReminderEmailRequest) -> SendReminderEmailResponse:
    """
    Email one physician their current, non-overridden shift-request
    validation errors. Only ever runs from an explicit "Send Reminder
    Email" click in the app — nothing calls this automatically.
    """
    config = email_sender.load_email_config()
    if config is None or not email_sender.is_fully_configured(config):
        raise HTTPException(
            status_code=400,
            detail="Email sending is not configured. Copy scheduler/config/email_template.yaml "
                   "to email.yaml (in the physician config folder) and fill it in.",
        )

    submissions: list[PhysicianSubmission] = _state.get("submissions") or []
    roster = _state.get("roster") or {}
    sub = next((s for s in submissions if s.physician_id == body.physician_id), None)
    if sub is None:
        raise HTTPException(status_code=404, detail=f"No submission for physician_id {body.physician_id!r}")

    physician_cfg = roster.get(body.physician_id)
    to_address = physician_cfg.email if physician_cfg else ""
    if not to_address:
        raise HTTPException(
            status_code=400,
            detail="No email on file for this physician — add one in the Physician Roster editor.",
        )

    index = build_alias_index(roster)
    unresolved = {body.physician_id} if _resolve_submission_id(sub, index) is None else set()
    [result] = _build_import_results([sub], unresolved, roster)
    errors = [i.message for i in result.issues if i.severity == "error" and not i.overridden]
    if not errors:
        return SendReminderEmailResponse(ok=False, status="No current validation errors for this physician.")

    subject = f"Shift Request Follow-up — {result.physician_name}"
    body_text = (
        f"Hi {result.physician_name},\n\n"
        f"Your shift request submission for {sub.year}-{sub.month:02d} has the following "
        f"issue(s) that need to be corrected:\n\n"
        + "\n".join(f"  - {e}" for e in errors)
        + "\n\nPlease revise and resubmit your preferences.\n"
    )

    try:
        email_sender.send_email(to_address, subject, body_text, config)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return SendReminderEmailResponse(ok=True, status=f"Sent to {to_address}.")


# ---------------------------------------------------------------------------
# Monthly shift requests — sked roster sync + magic links + email
# ---------------------------------------------------------------------------
# "Send Monthly Shift Requests": push the active roster and that month's
# master schedule template to sked, get back one signed magic link per
# physician, then email each link out via the same SMTP setup as reminder
# emails. sked never sends email itself. Only ever runs from an explicit
# click in the app, same as the reminder-email flow above.

@app.get("/api/sked/status", response_model=SkedStatusResponse)
def sked_status() -> SkedStatusResponse:
    """Whether sked (the shift-preference site) connection is configured."""
    config = sked_client.load_sked_config()
    configured = config is not None and sked_client.is_fully_configured(config)
    return SkedStatusResponse(configured=configured)


@app.get("/api/sked/periods", response_model=PeriodsResponse)
def sked_periods(kind: str = "shift_request") -> PeriodsResponse:
    """
    List existing sked periods, newest first — for the "view a physician's
    preference sheet" picker (RosterEditor's Preferences section), so the
    scheduler picks a real period instead of guessing an id format. Default
    kind="shift_request" (the monthly grid); pass kind=survey for the annual
    survey's periods (already separately listed via /api/sked/surveys, but
    exposed here too since this endpoint is more general).
    """
    sked_config = sked_client.load_sked_config()
    if sked_config is None or not sked_client.is_fully_configured(sked_config):
        raise HTTPException(
            status_code=400,
            detail="sked is not configured. Copy scheduler/config/sked_template.yaml to sked.yaml "
                   "(in the physician config folder) and fill it in.",
        )
    try:
        periods = sked_client.list_periods(sked_config, kind=kind)
    except sked_client.SkedApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return PeriodsResponse(periods=[
        PeriodInfo(id=p["id"], label=p["label"], opens_at=p["opens_at"], closes_at=p["closes_at"])
        for p in periods
    ])


@app.post("/api/sked/physician-link", response_model=PhysicianLinkResponse)
def sked_physician_link(body: PhysicianLinkRequest) -> PhysicianLinkResponse:
    """
    Generate (or regenerate) one physician's magic link for an existing sked
    period, for the scheduler to open and view directly — never emailed,
    unlike /api/monthly-requests/send and /api/sked/survey/resend. Safe to
    call repeatedly: sked signs a fresh token against the physician's
    already-saved submission each time, nothing about their data changes.
    """
    sked_config = sked_client.load_sked_config()
    if sked_config is None or not sked_client.is_fully_configured(sked_config):
        raise HTTPException(
            status_code=400,
            detail="sked is not configured. Copy scheduler/config/sked_template.yaml to sked.yaml "
                   "(in the physician config folder) and fill it in.",
        )
    roster: dict = _state.get("roster") or {}
    if not roster:
        try:
            roster = load_roster()
            _state["roster"] = roster
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not load physician roster: {exc}")

    cfg = roster.get(body.physician_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Unknown physician: {body.physician_id!r}")

    # A survey period lives on a different Custom Domain (survey.keatools.org,
    # rewritten to /survey.html by sked's Worker) than a shift-request period
    # (sked.keatools.org root) -- without checking kind, every link generated
    # here pointed at the shift-request grid regardless of which kind of
    # period was actually asked for, silently producing a broken link for any
    # survey period.
    try:
        periods = sked_client.list_periods(sked_config)
    except sked_client.SkedApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    period = next((p for p in periods if p["id"] == body.period_id), None)
    if period is None:
        raise HTTPException(status_code=404, detail=f"Unknown period: {body.period_id!r}")
    is_survey = period.get("kind") == "survey"

    try:
        links = sked_client.generate_period_links(
            sked_config,
            body.period_id,
            [
                {
                    "id": cfg.id,
                    "name": _display_name(cfg),
                    "email": cfg.email,
                    "maxConsecutiveShifts": cfg.max_consecutive_shifts,
                    "maxConsecutiveNights": cfg.max_consecutive_nights,
                    "nonAcuteSitePreference": cfg.group_b_site_preference or "",
                }
            ],
            base_url_override=(sked_config.survey_base_url or None) if is_survey else None,
        )
    except sked_client.SkedApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if not links:
        raise HTTPException(status_code=502, detail="sked returned no link for this physician.")
    return PhysicianLinkResponse(url=links[0]["url"])


@app.post("/api/monthly-requests/send", response_model=SendMonthlyRequestsResponse)
def send_monthly_requests(body: SendMonthlyRequestsRequest) -> SendMonthlyRequestsResponse:
    sked_config = sked_client.load_sked_config()
    if sked_config is None or not sked_client.is_fully_configured(sked_config):
        raise HTTPException(
            status_code=400,
            detail="sked is not configured. Copy scheduler/config/sked_template.yaml to sked.yaml "
                   "(in the physician config folder) and fill it in.",
        )
    email_config = email_sender.load_email_config()
    if email_config is None or not email_sender.is_fully_configured(email_config):
        raise HTTPException(
            status_code=400,
            detail="Email sending is not configured. Copy scheduler/config/email_template.yaml "
                   "to email.yaml (in the physician config folder) and fill it in.",
        )

    template_path: Path | None = None
    if body.template_path:
        template_path = Path(body.template_path)
        if not template_path.is_file():
            raise HTTPException(status_code=400, detail=f"Template file not found: {body.template_path}")

    roster: dict = _state.get("roster") or {}
    if not roster:
        # This is often the first action of a new monthly cycle, before
        # anything else has loaded the roster into _state (see the same
        # pattern in load_schedule() above).
        try:
            roster = load_roster()
            _state["roster"] = roster
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not load physician roster: {exc}")

    active_physicians = [cfg for cfg in roster.values() if cfg.active]
    if not active_physicians:
        raise HTTPException(status_code=400, detail="No active physicians in the roster.")
    if body.physician_ids:
        wanted = set(body.physician_ids)
        active_physicians = [cfg for cfg in active_physicians if cfg.id in wanted]
        if not active_physicians:
            raise HTTPException(status_code=400, detail="None of the selected physicians are active in the roster.")

    try:
        sked_client.upsert_period(sked_config, body.period_id, body.label, body.opens_at, body.closes_at)
        if template_path is not None:
            sked_client.upload_period_template(sked_config, body.period_id, template_path)
        # else: reuse whatever's already uploaded to this period on sked --
        # the "resend / target specific people on an existing period" path.
        links = sked_client.generate_period_links(
            sked_config,
            body.period_id,
            [
                {
                    "id": cfg.id,
                    "name": _display_name(cfg),
                    "email": cfg.email,
                    "maxConsecutiveShifts": cfg.max_consecutive_shifts,
                    "maxConsecutiveNights": cfg.max_consecutive_nights,
                    "nonAcuteSitePreference": cfg.group_b_site_preference or "",
                }
                for cfg in active_physicians
            ],
        )
    except sked_client.SkedApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    results: list[SendMonthlyRequestsResult] = []
    for link in links:
        physician_id, name, email, url, has_email = (
            link["id"], link["name"], link["email"], link["url"], link["hasEmail"],
        )
        if not has_email:
            results.append(SendMonthlyRequestsResult(
                physician_id=physician_id, physician_name=name, email="",
                status="no_email", detail="No email on file — add one in the Physician Roster editor.",
            ))
            continue

        extra = body.extra_message.strip()
        subject = f"{body.label} — Shift Preferences"
        body_text = (
            f"Hi {name},\n\n"
            f"Please enter your shift preferences for {body.label} using the link below:\n\n"
            f"{url}\n\n"
            f"This link is unique to you — please don't forward it. It saves your progress "
            f"automatically, so you can close the window and come back to this same link "
            f"any time to pick up where you left off, right up until the submission window closes.\n"
            + (f"\n{extra}\n" if extra else "")
        )
        try:
            email_sender.send_email(email, subject, body_text, email_config)
            results.append(SendMonthlyRequestsResult(
                physician_id=physician_id, physician_name=name, email=email, status="sent",
            ))
        except RuntimeError as exc:
            results.append(SendMonthlyRequestsResult(
                physician_id=physician_id, physician_name=name, email=email,
                status="send_failed", detail=str(exc),
            ))

    sent_count = sum(1 for r in results if r.status == "sent")
    needs_attention = [r for r in results if r.status != "sent"]
    return SendMonthlyRequestsResponse(
        ok=True, sent_count=sent_count, results=results, needs_attention=needs_attention,
    )


@app.post("/api/monthly-requests/resend", response_model=ResendMonthlyRequestResponse)
def resend_monthly_request(body: ResendMonthlyRequestRequest) -> ResendMonthlyRequestResponse:
    """Regenerate one physician's shift-request link and email it. Only ever runs from an explicit click."""
    sked_config = sked_client.load_sked_config()
    if sked_config is None or not sked_client.is_fully_configured(sked_config):
        raise HTTPException(
            status_code=400,
            detail="sked is not configured. Copy scheduler/config/sked_template.yaml to sked.yaml "
                   "(in the physician config folder) and fill it in.",
        )
    email_config = email_sender.load_email_config()
    if email_config is None or not email_sender.is_fully_configured(email_config):
        raise HTTPException(
            status_code=400,
            detail="Email sending is not configured. Copy scheduler/config/email_template.yaml "
                   "to email.yaml (in the physician config folder) and fill it in.",
        )

    roster: dict = _state.get("roster") or {}
    if not roster:
        try:
            roster = load_roster()
            _state["roster"] = roster
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not load physician roster: {exc}")

    cfg = roster.get(body.physician_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Unknown physician: {body.physician_id!r}")
    if not cfg.email:
        raise HTTPException(
            status_code=400,
            detail="No email on file for this physician — add one in the Physician Roster editor.",
        )

    try:
        periods = sked_client.list_periods(sked_config, kind="shift_request")
    except sked_client.SkedApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    period = next((p for p in periods if p["id"] == body.period_id), None)
    if period is None:
        raise HTTPException(status_code=404, detail=f"Unknown period: {body.period_id!r}")

    name = _display_name(cfg)
    try:
        links = sked_client.generate_period_links(
            sked_config, body.period_id, [{"id": cfg.id, "name": name, "email": cfg.email}],
        )
    except sked_client.SkedApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    url = links[0]["url"]

    subject = f"{period['label']} — Reminder"
    body_text = (
        f"Hi {name},\n\n"
        f"Here is your link to enter your shift preferences for {period['label']}:\n\n"
        f"{url}\n\n"
        f"This link is unique to you — please don't forward it. It saves your progress "
        f"automatically, so you can close the window and come back to this same link any "
        f"time to review or update your answers.\n"
    )
    try:
        email_sender.send_email(cfg.email, subject, body_text, email_config)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return ResendMonthlyRequestResponse(ok=True, status="sent", detail=f"Sent to {cfg.email}.")


@app.post("/api/sked/import", response_model=ImportDirectoryResponse)
def sked_import(body: SkedImportRequest) -> ImportDirectoryResponse:
    """
    Pull in shift-preference submissions directly from sked for one period,
    instead of a directory/flat file of xlsx exports. For each active roster
    physician with a submission (draft or submitted), sked rebuilds their
    filled-preferences xlsx server-side and this reuses the exact same
    parsing path as /api/import (importer.import_single_file) — nothing
    downstream (validation, generation) needs to know the source differs.
    Physicians with no submission yet, or still in "draft", come back in
    not_submitted instead of being silently skipped.
    """
    sked_config = sked_client.load_sked_config()
    if sked_config is None or not sked_client.is_fully_configured(sked_config):
        raise HTTPException(
            status_code=400,
            detail="sked is not configured. Copy scheduler/config/sked_template.yaml to sked.yaml "
                   "(in the physician config folder) and fill it in.",
        )

    roster = load_roster()
    scheduler_cfg = _load_scheduler_config()
    active_physicians = {cfg.id: cfg for cfg in roster.values() if cfg.active}

    try:
        rows = sked_client.list_period_submissions(sked_config, body.period_id)
    except sked_client.SkedApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    by_physician = {r["physicianId"]: r for r in rows}

    not_submitted: list[NotSubmittedRow] = []
    submissions: list[PhysicianSubmission] = []
    fetch_errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="sked-import-") as tmpdir:
        for physician_id, cfg in active_physicians.items():
            row = by_physician.get(physician_id)
            if row is None:
                not_submitted.append(NotSubmittedRow(
                    physician_id=physician_id, physician_name=_display_name(cfg),
                    status="not_started", email=cfg.email,
                ))
                continue
            if row["status"] != "submitted":
                not_submitted.append(NotSubmittedRow(
                    physician_id=physician_id, physician_name=_display_name(cfg),
                    status="draft", email=cfg.email,
                ))
                continue
            try:
                xlsx_bytes = sked_client.fetch_physician_export(sked_config, physician_id, body.period_id)
            except sked_client.SkedApiError as exc:
                fetch_errors.append(f"{_display_name(cfg)}: could not fetch from sked ({exc})")
                continue
            file_path = Path(tmpdir) / f"{physician_id}.xlsx"
            file_path.write_bytes(xlsx_bytes)
            try:
                sub = import_single_file(file_path, body.year, body.month, physician_id_override=physician_id)
                submissions.append(sub)
            except Exception as exc:
                fetch_errors.append(f"{_display_name(cfg)}: could not parse submission ({exc})")

        _state["overrides"] = {}
        unresolved = _apply_roster(submissions, roster)
        _auto_override_flagged_physicians(submissions, roster)
        _apply_shift_count_overrides(submissions, roster)
        _apply_casual_availability_default(submissions, roster)
        results = _build_import_results(submissions, unresolved, roster)
        _state.update(submissions=submissions, roster=roster, scheduler_config=scheduler_cfg,
                      year=body.year, month=body.month, directory=f"sked:{body.period_id}", source_file=None)

    for err in fetch_errors:
        print(f"[sked_import] WARNING: {err}")

    valid_count = sum(1 for r in results if r.is_valid)
    return ImportDirectoryResponse(
        year=body.year, month=body.month, directory=f"sked:{body.period_id}",
        physicians=results, total_physicians=len(results), valid_physicians=valid_count,
        not_submitted=not_submitted,
    )


# ---------------------------------------------------------------------------
# Annual survey — completion tracking (viewer only)
# ---------------------------------------------------------------------------
# Encoding free-text requests into CP-SAT solver rules is done by hand
# (scheduler + Claude), not by this app -- these endpoints only let the
# scheduler see who has/hasn't completed the survey and pull the raw data.

@app.get("/api/sked/surveys", response_model=SurveysResponse)
def sked_surveys() -> SurveysResponse:
    sked_config = sked_client.load_sked_config()
    if sked_config is None or not sked_client.is_fully_configured(sked_config):
        raise HTTPException(
            status_code=400,
            detail="sked is not configured. Copy scheduler/config/sked_template.yaml to sked.yaml "
                   "(in the physician config folder) and fill it in.",
        )
    try:
        surveys = sked_client.list_surveys(sked_config)
    except sked_client.SkedApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return SurveysResponse(surveys=[
        SurveyInfo(id=s["id"], label=s["label"], opens_at=s["opens_at"], closes_at=s["closes_at"])
        for s in surveys
    ])


@app.get("/api/sked/survey-completion", response_model=SurveyCompletionResponse)
def sked_survey_completion(survey_id: str) -> SurveyCompletionResponse:
    sked_config = sked_client.load_sked_config()
    if sked_config is None or not sked_client.is_fully_configured(sked_config):
        raise HTTPException(
            status_code=400,
            detail="sked is not configured. Copy scheduler/config/sked_template.yaml to sked.yaml "
                   "(in the physician config folder) and fill it in.",
        )

    roster: dict = _state.get("roster") or {}
    if not roster:
        try:
            roster = load_roster()
            _state["roster"] = roster
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not load physician roster: {exc}")

    try:
        surveys = sked_client.list_surveys(sked_config)
        responses = sked_client.get_survey_responses(sked_config, survey_id)
    except sked_client.SkedApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    survey = next((s for s in surveys if s["id"] == survey_id), None)
    if survey is None:
        raise HTTPException(status_code=404, detail=f"Unknown survey: {survey_id!r}")

    by_physician = {r["physicianId"]: r for r in responses}
    rows: list[SurveyCompletionRow] = []
    for cfg in sorted(roster.values(), key=lambda c: c.last_name or c.name):
        if not cfg.active:
            continue
        r = by_physician.get(cfg.id)
        rows.append(SurveyCompletionRow(
            physician_id=cfg.id,
            physician_name=_display_name(cfg),
            active=cfg.active,
            status=r["status"] if r else "not_started",
            updated_at=r["updatedAt"] if r else None,
            data=r["data"] if r else None,
        ))

    return SurveyCompletionResponse(
        survey=SurveyInfo(id=survey["id"], label=survey["label"], opens_at=survey["opens_at"], closes_at=survey["closes_at"]),
        rows=rows,
        submitted_count=sum(1 for row in rows if row.status == "submitted"),
        total_active=len(rows),
    )


@app.post("/api/sked/survey/resend", response_model=ResendSurveyLinkResponse)
def resend_survey_link(body: ResendSurveyLinkRequest) -> ResendSurveyLinkResponse:
    """Regenerate one physician's survey link and email it. Only ever runs from an explicit click."""
    sked_config = sked_client.load_sked_config()
    if sked_config is None or not sked_client.is_fully_configured(sked_config):
        raise HTTPException(
            status_code=400,
            detail="sked is not configured. Copy scheduler/config/sked_template.yaml to sked.yaml "
                   "(in the physician config folder) and fill it in.",
        )
    email_config = email_sender.load_email_config()
    if email_config is None or not email_sender.is_fully_configured(email_config):
        raise HTTPException(
            status_code=400,
            detail="Email sending is not configured. Copy scheduler/config/email_template.yaml "
                   "to email.yaml (in the physician config folder) and fill it in.",
        )

    roster: dict = _state.get("roster") or {}
    if not roster:
        try:
            roster = load_roster()
            _state["roster"] = roster
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not load physician roster: {exc}")

    cfg = roster.get(body.physician_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Unknown physician: {body.physician_id!r}")
    if not cfg.email:
        raise HTTPException(
            status_code=400,
            detail="No email on file for this physician — add one in the Physician Roster editor.",
        )

    try:
        surveys = sked_client.list_surveys(sked_config)
    except sked_client.SkedApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    survey = next((s for s in surveys if s["id"] == body.survey_id), None)
    if survey is None:
        raise HTTPException(status_code=404, detail=f"Unknown survey: {body.survey_id!r}")

    name = _display_name(cfg)
    try:
        links = sked_client.generate_period_links(
            sked_config,
            body.survey_id,
            [{"id": cfg.id, "name": name, "email": cfg.email}],
            base_url_override=sked_config.survey_base_url or None,
        )
    except sked_client.SkedApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    url = links[0]["url"]

    subject = f"{survey['label']} — Reminder"
    body_text = (
        f"Hi {name},\n\n"
        f"Here is your link to complete the {survey['label']}:\n\n"
        f"{url}\n\n"
        f"This link is unique to you — please don't forward it. It saves your progress "
        f"automatically, so you can close the window and come back to this same link any "
        f"time to review or update your answers, even after submitting.\n"
    )
    try:
        email_sender.send_email(cfg.email, subject, body_text, email_config)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return ResendSurveyLinkResponse(ok=True, status="sent", detail=f"Sent to {cfg.email}.")


# ---------------------------------------------------------------------------
# ByteBloc integration
# ---------------------------------------------------------------------------
# See scheduler/backend/bytebloc.py for the hard safety rule this all sits
# behind: nothing is sent to ByteBloc without a human typing the exact
# confirmation phrase into the send request, checked again server-side.

def _eligible_submissions_for_bytebloc() -> tuple[list[PhysicianSubmission], int]:
    """
    Physician submissions that currently pass validation (no remaining
    non-overridden errors) — the only ones ever considered for a ByteBloc
    request. Returns (eligible, excluded_count).
    """
    submissions: list[PhysicianSubmission] = _state.get("submissions") or []
    roster = _state.get("roster") or {}
    index = build_alias_index(roster)
    unresolved = {
        sub.physician_id for sub in submissions
        if _resolve_submission_id(sub, index) is None
    }
    results = _build_import_results(submissions, unresolved, roster)
    valid_ids = {r.physician_id for r in results if r.is_valid}
    eligible = [s for s in submissions if s.physician_id in valid_ids]
    return eligible, len(submissions) - len(eligible)


def _build_bytebloc_payload(
    use_delta: bool = True,
) -> tuple[bytebloc_mod.ByteBlocConfig | None, dict, list[str], list, bool, int]:
    """
    Shared by preview and send so both always agree on exactly what would
    be sent. Returns (config, payload, warnings, preview_items, used_delta,
    skipped_unchanged_count) -- used_delta is False whenever use_delta was
    False, or there was nothing on record yet for this period to diff
    against (a plain full send either way, just worth the caller knowing
    which case it was).
    """
    config = bytebloc_mod.load_bytebloc_config()
    if config is None:
        return None, {}, [
            "ByteBloc is not configured yet. Copy "
            "scheduler/config/bytebloc_template.yaml to bytebloc.yaml and fill it in."
        ], [], False, 0

    year, month = _state.get("year"), _state.get("month")
    if not year or not month:
        return config, {}, ["Import and validate a month's submissions first."], [], False, 0

    eligible, excluded = _eligible_submissions_for_bytebloc()
    roster = _state.get("roster") or {}
    display_names = build_display_names(roster)

    last_sent = bytebloc_mod.load_last_sent(year, month) if use_delta else {}
    applied_delta = use_delta and bool(last_sent)
    payload, warnings, preview_items, skipped = bytebloc_mod.build_shift_requests_payload(
        eligible, config, year, month, display_names,
        last_sent=last_sent if applied_delta else None,
    )
    if excluded:
        warnings.insert(
            0,
            f"{excluded} physician(s) with unresolved validation errors were excluded "
            f"from this request.",
        )
    if not bytebloc_mod.is_fully_configured(config):
        warnings.insert(0, "ByteBloc connection details in bytebloc.yaml are incomplete.")
    return config, payload, warnings, preview_items, applied_delta, skipped


@app.get("/api/bytebloc/preview", response_model=ByteBlocPreviewResponse)
def bytebloc_preview(use_delta: bool = True) -> ByteBlocPreviewResponse:
    """
    Build (but never send) the ByteBloc createShiftRequests payload from
    the current, currently-valid physician submissions. Read-only — this
    never contacts ByteBloc.

    use_delta=True (default) only includes cells that differ from this
    backend instance's own record of what it last successfully sent for
    this period — see bytebloc.py's load_last_sent for what that instance
    scoping does and doesn't cover. use_delta=False always includes every
    mapped cell, a full resend.
    """
    config, payload, warnings, preview_items, used_delta, skipped = _build_bytebloc_payload(use_delta)
    if config is None:
        return ByteBlocPreviewResponse(configured=False, warnings=warnings)

    by_physician: dict[str, ByteBlocPhysicianSummary] = {}
    for item in preview_items:
        row = by_physician.setdefault(
            item.physician_id,
            ByteBlocPhysicianSummary(physician_id=item.physician_id, physician_name=item.physician_name),
        )
        row.count += 1
        if item.off_type == "NeedOff":
            row.need_off_count += 1
        else:
            row.available_count += 1

    return ByteBlocPreviewResponse(
        configured=True,
        group_code=config.group_code,
        location_code=config.location_code,
        requester_id=config.requester_id,
        sked_start_date=payload.get("SkedStartDate", ""),
        by_physician=sorted(by_physician.values(), key=lambda r: r.physician_name),
        warnings=warnings,
        physician_count=len(by_physician),
        request_count=len(preview_items),
        need_off_count=sum(1 for item in preview_items if item.off_type == "NeedOff"),
        available_count=sum(1 for item in preview_items if item.off_type == "Available"),
        used_delta=used_delta,
        skipped_unchanged_count=skipped,
    )


@app.post("/api/bytebloc/send", response_model=ByteBlocSendResponse)
def bytebloc_send(body: ByteBlocSendRequest) -> ByteBlocSendResponse:
    """
    Actually POST the current ByteBloc payload to createShiftRequests.

    SAFETY: this is the only code path in the app allowed to contact
    ByteBloc's write API. It refuses unless body.confirmation is exactly
    bytebloc.CONFIRMATION_PHRASE, typed by a human into the confirmation
    dialog — do not add another caller, and do not weaken this check.
    The payload is rebuilt fresh from current state rather than trusting
    anything cached from a prior preview call.
    """
    if body.confirmation != bytebloc_mod.CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f'Type "{bytebloc_mod.CONFIRMATION_PHRASE}" exactly to confirm.',
        )

    config, payload, warnings, preview_items, used_delta, skipped = _build_bytebloc_payload(body.use_delta)
    if config is None:
        raise HTTPException(status_code=400, detail="ByteBloc is not configured.")
    if not bytebloc_mod.is_fully_configured(config):
        raise HTTPException(status_code=400, detail="ByteBloc connection details are incomplete.")
    if not preview_items:
        raise HTTPException(status_code=400, detail="There is nothing to send.")

    try:
        result = bytebloc_mod.send_shift_requests(payload, config, body.confirmation)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    status = str(result.get("Status", "unknown"))
    ok = status == "OK"
    if ok:
        # Only record what was actually just sent, and only on genuine
        # success -- see record_sent()'s own docstring for why a failed or
        # rejected send must never be recorded.
        year, month = _state.get("year"), _state.get("month")
        if year and month:
            bytebloc_mod.record_sent(year, month, preview_items)
    return ByteBlocSendResponse(ok=ok, status=status, raw=result)


@app.post("/api/generate-cancel")
def cancel_generate() -> dict:
    """Request that an in-progress /api/generate stop early and return its best-found result."""
    _state["cancel_requested"] = True
    return {"cancelled": True}


@app.get("/api/generate-progress")
def get_generate_progress() -> dict:
    return _state["progress"]


@app.post("/api/generate", response_model=ScheduleResponse)
async def generate(body: GenerateCachedRequest) -> ScheduleResponse:
    """
    Generate a schedule from previously imported submissions (runs 300 iterations).
    Call /api/import or /api/import-flat first.
    Poll /api/generate-progress for live iteration count.
    """
    submissions: list[PhysicianSubmission] = _state["submissions"]
    if not submissions or _state["year"] != body.year or _state["month"] != body.month:
        raise HTTPException(
            status_code=400,
            detail=f"No imported submissions for {body.year}-{body.month:02d}. "
                   "Call /api/import or /api/import-flat first.",
        )

    roster = _state["roster"]
    cfg = _state["scheduler_config"]
    use_cpsat = _CPSAT_AVAILABLE
    n_iterations = 400
    cpsat_time_limit = float(body.time_limit_seconds) if body.time_limit_seconds else 600.0
    _state["cancel_requested"] = False

    def cancel_check() -> bool:
        return _state["cancel_requested"]

    if use_cpsat:
        # CP-SAT: indeterminate progress — show 50% "Solving…" until done.
        _state["progress"] = {"current": 50, "total": 100, "running": True, "best_unfilled": None, "solver": "cpsat", "time_limit": int(cpsat_time_limit)}
    else:
        _state["progress"] = {"current": 0, "total": n_iterations, "running": True, "best_unfilled": None, "solver": "greedy"}

    def progress_cb(current: int, total: int, best_score: float) -> None:
        _state["progress"]["current"] = current
        # Derive approximate unfilled count from score: score = -unfilled*1000 + ...
        # Just show the raw best score for now. best_score can be +/-inf if
        # the solver hasn't found any solution yet — round()/int() can't
        # convert that, so fall back to None (unknown) rather than crashing.
        if math.isfinite(best_score):
            _state["progress"]["best_unfilled"] = round(-best_score / 1000)
        else:
            _state["progress"]["best_unfilled"] = None

    try:
        if use_cpsat:
            gen = CpsatScheduleGenerator(submissions, roster, cfg)
            result = await asyncio.to_thread(
                gen.generate, body.year, body.month, cpsat_time_limit,
                progress_callback=progress_cb, cancel_check=cancel_check,
            )
        else:
            gen = ScheduleGenerator(submissions, roster, cfg)
            result = await asyncio.to_thread(
                gen.run_best_of, n_iterations, body.year, body.month, progress_cb
            )
        # Preserve CP-SAT solver quality fields — repair_pass/_compute_stats creates
        # a fresh ScheduleStats that loses these, so we save and restore them.
        _solver_status = result.stats.solver_status if result.stats else None
        _optimality_gap_pct = result.stats.optimality_gap_pct if result.stats else None
        # Post-solve repair: juggle adjacent assignments to fill remaining gaps
        if result.unfilled:
            result = await asyncio.to_thread(gen.repair_pass, result, 50)
        # Sync issues list: only keep entries for slots that remain unfilled
        # (repair pass may have filled slots that still appear in issues)
        unfilled_keys = {
            f"{u.date.strftime('%b %d')} {u.shift.code}" for u in result.unfilled
        }
        result.issues = [i for i in result.issues if any(k in i for k in unfilled_keys)] \
            if result.unfilled else []
        # Assign on-call shifts after the regular schedule is complete
        result = await asyncio.to_thread(gen.assign_on_calls, result)
        # Restore solver quality fields lost by repair_pass/_compute_stats
        if result.stats and _solver_status:
            result.stats.solver_status = _solver_status
            result.stats.optimality_gap_pct = _optimality_gap_pct
    except Exception as exc:
        _state["progress"]["running"] = False
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}\n{traceback.format_exc()}")

    final_total = 100 if use_cpsat else n_iterations
    _state["progress"] = {"current": final_total, "total": final_total, "running": False, "best_unfilled": len(result.unfilled)}
    _state["generator"] = gen
    _state["result"] = result

    return _result_to_response(result)


@app.get("/api/schedule", response_model=ScheduleResponse)
def get_schedule() -> ScheduleResponse:
    """Return the most recently generated schedule."""
    result: ScheduleResult | None = _state.get("result")
    if result is None:
        raise HTTPException(status_code=404, detail="No schedule generated yet.")
    return _result_to_response(result)




@app.get("/api/export")
def export_schedule() -> StreamingResponse:
    """Export the current schedule as an Excel file in the same layout as the human schedule."""
    result: ScheduleResult | None = _state.get("result")
    if result is None:
        raise HTTPException(status_code=404, detail="No schedule generated yet.")

    wb = _build_export_workbook(result)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"schedule_{result.year}_{result.month:02d}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Shift rows in display order: (site_label, time_label, time_code, site_code)
# time_code/site_code == None means it is an on-call row filled from result.on_calls.
_EXPORT_SHIFTS = [
    ("DOC",       "0500-1559",  None,    None),           # Day on call row — same hour range the human schedule uses
    ("RAH A",     "0600-1200",  "0600h",  "RAH A side"),
    ("RAH B",     "0600-1200",  "0600h",  "RAH B side"),
    ("NECHC",     "0600-1400",  "0600h",  "NEHC"),
    ("RAH I",     "0600-1400",  "0600h",  "RAH I side"),
    ("NECHC",     "0900-1700",  "0900h",  "NEHC"),
    ("RAH I",     "1000-1800",  "1000h",  "RAH I side"),
    ("RAH A",     "1200-1800",  "1200h",  "RAH A side"),
    ("RAH B",     "1200-1800",  "1200h",  "RAH B side"),
    ("NECHC",     "1200-2000",  "1200h",  "NEHC"),
    ("RAH I",     "1400-2200",  "1400h",  "RAH I side"),
    ("NECHC",     "1500-2300",  "1500h",  "NEHC"),
    ("NOC",       "1600-0459",  None,  None),           # Night on call row — same hour range the human schedule uses
    ("RAH Float", "1600-0459",  "1600h",  "RAH F side"),
    ("NECHC",     "1700-0100",  "1700h",  "NEHC"),
    ("RAH A",     "1800-0000",  "1800h",  "RAH A side"),
    ("RAH B",     "1800-0000",  "1800h",  "RAH B side"),
    ("RAH I",     "1800-0200",  "1800h",  "RAH I side"),
    ("NECHC",     "2000-0400",  "2000h",  "NEHC"),
    ("RAH A",     "2400-0600",  "2400h",  "RAH A side"),
    ("RAH B",     "2400-0600",  "2400h",  "RAH B side"),
    ("NECHC",     "2400-0800",  "2400h",  "NEHC"),
    ("RAH I",     "2400-0800",  "2400h",  "RAH I side"),
]

_HDR_FILL  = PatternFill("solid", fgColor="1E293B")
_HDR_FONT  = Font(bold=True, color="FFFFFF", size=9)
_DATE_FILL = PatternFill("solid", fgColor="334155")
_DATE_FONT = Font(bold=True, color="FFFFFF", size=9)
_LABEL_FONT = Font(bold=True, size=9)
_TIME_FONT  = Font(italic=True, color="64748B", size=8)
_CELL_FONT  = Font(size=9)
_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=False)
_LEFT   = Alignment(horizontal="left",   vertical="center")
_THIN   = Side(style="thin", color="CBD5E1")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

_NIGHT_FILL = PatternFill("solid", fgColor="EFF6FF")   # light blue for 2400h rows
_AM_FILL    = PatternFill("solid", fgColor="F0FDF4")   # light green for 0600h rows


def _build_export_workbook(result: ScheduleResult) -> openpyxl.Workbook:
    # Index regular assignments by (date, shift_code) -> physician_name
    index: dict[tuple, str] = {
        (a.date, a.shift.code): a.physician_name
        for a in result.assignments
    }
    # Index on-call assignments by (date, call_type) -> physician_name
    call_index: dict[tuple, str] = {
        (oc.date, oc.call_type): oc.physician_name
        for oc in result.on_calls
    }

    days_in_month = calendar.monthrange(result.year, result.month)[1]
    all_dates = [datetime.date(result.year, result.month, d) for d in range(1, days_in_month + 1)]

    # Group dates into Sun-starting weeks
    weeks: list[list[datetime.date | None]] = []
    # Find the first Sunday on or before the 1st
    first = all_dates[0]
    week_start = first - datetime.timedelta(days=(first.weekday() + 1) % 7)
    d = week_start
    while d <= all_dates[-1]:
        week = []
        for i in range(7):
            day = d + datetime.timedelta(days=i)
            week.append(day if day.month == result.month else None)
        weeks.append(week)
        d += datetime.timedelta(days=7)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"{result.year}-{result.month:02d}"

    # Column widths: col A = label (12), cols B-H = day columns (11 each), col I = repeat label
    ws.column_dimensions["A"].width = 12
    for col_letter in ["B","C","D","E","F","G","H"]:
        ws.column_dimensions[col_letter].width = 11
    ws.column_dimensions["I"].width = 12

    row = 1
    day_names = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]

    for week_dates in weeks:
        # ── Week header: day names ──
        ws.row_dimensions[row].height = 14
        ws.cell(row, 1).fill = _HDR_FILL
        for col, name in enumerate(day_names, start=2):
            c = ws.cell(row, col, name)
            c.font = _HDR_FONT; c.fill = _HDR_FILL; c.alignment = _CENTER; c.border = _BORDER
        ws.cell(row, 9).fill = _HDR_FILL
        row += 1

        # ── Date numbers ──
        ws.row_dimensions[row].height = 13
        ws.cell(row, 1).fill = _DATE_FILL
        for col, d in enumerate(week_dates, start=2):
            c = ws.cell(row, col, d.day if d else "")
            c.font = _DATE_FONT; c.fill = _DATE_FILL; c.alignment = _CENTER; c.border = _BORDER
        ws.cell(row, 9).fill = _DATE_FILL
        row += 1

        # ── Shift rows (2 rows per shift: physician name + time range) ──
        for site_label, time_label, time_code, site_code in _EXPORT_SHIFTS:
            is_night  = time_code == "2400h"
            is_am     = time_code == "0600h"
            is_oncall = time_code is None   # DOC or NOC row

            # Row A: site label + physician names
            ws.row_dimensions[row].height = 14
            c = ws.cell(row, 1, site_label)
            c.font = _LABEL_FONT; c.alignment = _LEFT; c.border = _BORDER
            if is_night: c.fill = _NIGHT_FILL
            elif is_am:  c.fill = _AM_FILL

            for col, d in enumerate(week_dates, start=2):
                name = ""
                if d:
                    if is_oncall:
                        # DOC or NOC — look up from on-call index
                        name = call_index.get((d, site_label), "")
                    elif time_code and site_code:
                        shift_code = f"{time_code} {site_code}"
                        name = index.get((d, shift_code), "")
                c2 = ws.cell(row, col, name)
                c2.font = _CELL_FONT; c2.alignment = _CENTER; c2.border = _BORDER
                if is_night: c2.fill = _NIGHT_FILL
                elif is_am:  c2.fill = _AM_FILL

            # repeat label in col I
            c9 = ws.cell(row, 9, site_label)
            c9.font = _LABEL_FONT; c9.alignment = _LEFT; c9.border = _BORDER
            if is_night: c9.fill = _NIGHT_FILL
            elif is_am:  c9.fill = _AM_FILL
            row += 1

            # Row B: time range (greyed out)
            ws.row_dimensions[row].height = 11
            c = ws.cell(row, 1, time_label)
            c.font = _TIME_FONT; c.alignment = _LEFT; c.border = _BORDER
            for col in range(2, 9):
                ws.cell(row, col).border = _BORDER
            ws.cell(row, 9, time_label).font = _TIME_FONT
            row += 1

        # Small gap row between weeks
        row += 1

    return wb


# Reverse lookup: (site_label, time_label) -> (time_code, site_code)
_EXPORT_SHIFT_LOOKUP: dict[tuple[str, str], tuple] = {
    (site_label, time_label): (time_code, site_code)
    for site_label, time_label, time_code, site_code in _EXPORT_SHIFTS
}
# Back-compat: files exported before on-call rows carried a real hour range
# used the literal label as the time row. Keep these loadable.
_EXPORT_SHIFT_LOOKUP[("DOC", "Day On Call")] = (None, None)
_EXPORT_SHIFT_LOOKUP[("NOC", "Night On Call")] = (None, None)

# Flat shift-code -> Shift object lookup (used by xlsx loader)
_SHIFT_CODE_LOOKUP: dict[str, Shift] = {
    shift.code: shift
    for block in BLOCKS
    for shift in block
}


def _parse_schedule_xlsx(path: Path, roster: dict) -> ScheduleResult:
    """
    Parse a previously exported schedule xlsx back into a ScheduleResult.

    Reconstructs assignments, on-calls, unfilled slots, and stats.
    Physician IDs are resolved from the roster by display name.
    Fields not stored in the xlsx (solver_status, optimality_gap_pct,
    candidate lists) are set to None / [].
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    # --- Year/month from sheet title (e.g. "2026-06") ---
    try:
        title = ws.title
        year, month = int(title[:4]), int(title[5:7])
    except Exception:
        raise ValueError(
            f"Cannot determine year/month from sheet title {ws.title!r}. "
            "Expected format: YYYY-MM."
        )

    # --- Name → ID reverse lookup (case-insensitive fallback) ---
    name_to_id: dict[str, str] = {}
    for pid, cfg in roster.items():
        name_to_id[cfg.name] = pid
        name_to_id[cfg.name.lower()] = pid

    def _resolve_id(name: str) -> str:
        return (
            name_to_id.get(name)
            or name_to_id.get(name.lower())
            or name
        )

    assignments: list[Assignment] = []
    on_calls: list[OnCallAssignment] = []
    unfilled: list[UnfilledSlot] = []

    rows = list(ws.iter_rows(values_only=True))
    i = 0
    while i < len(rows):
        row = rows[i]

        # Detect week header: col B (index 1) == "SUN"
        if row[1] != "SUN":
            i += 1
            continue

        # Next row is the date number row
        i += 1
        if i >= len(rows):
            break
        date_row = rows[i]
        col_to_date: dict[int, datetime.date] = {}
        for c in range(1, 8):   # cols B–H → indices 1–7
            val = date_row[c]
            if val is not None and val != "":
                try:
                    col_to_date[c] = datetime.date(year, month, int(val))
                except (ValueError, TypeError):
                    pass

        # Parse shift pairs until gap row or next week header
        i += 1
        while i < len(rows):
            site_row = rows[i]

            # Gap row (col A is None/empty) → end of week
            if site_row[0] is None or str(site_row[0]).strip() == "":
                i += 1
                break

            # site_row is Row A of a shift pair; next row is Row B (time label)
            i += 1
            if i >= len(rows):
                break
            time_row = rows[i]
            i += 1

            site_label = str(site_row[0]).strip()
            time_label = str(time_row[0]).strip() if time_row[0] is not None else ""

            entry = _EXPORT_SHIFT_LOOKUP.get((site_label, time_label))
            if not entry:
                continue
            time_code, site_code = entry

            for c, d in col_to_date.items():
                cell_val = site_row[c]
                name = str(cell_val).strip() if cell_val is not None else ""

                if time_code is None:
                    # On-call row (DOC / NOC)
                    if name and name not in ("", "---", "None"):
                        on_calls.append(OnCallAssignment(
                            date=d,
                            call_type=site_label,
                            physician_id=_resolve_id(name),
                            physician_name=name,
                        ))
                else:
                    shift_code = f"{time_code} {site_code}"
                    shift = _SHIFT_CODE_LOOKUP.get(shift_code)
                    if shift is None:
                        continue
                    if name and name not in ("", "---", "None"):
                        assignments.append(Assignment(
                            date=d,
                            shift=shift,
                            physician_id=_resolve_id(name),
                            physician_name=name,
                        ))
                    else:
                        unfilled.append(UnfilledSlot(date=d, shift=shift, candidates=[]))

    # --- Compute stats from reconstructed assignments ---
    filled = len(assignments)
    total = filled + len(unfilled)
    group_a = sum(1 for a in assignments if a.shift.site_group.value == "A")
    group_b = filled - group_a

    physician_counts: dict[str, int] = {}
    for a in assignments:
        physician_counts[a.physician_id] = physician_counts.get(a.physician_id, 0) + 1

    # Singleton 2400h detection (isolated night = no adjacent night within 1 day)
    nights_by_pid: dict[str, list[datetime.date]] = {}
    for a in assignments:
        if a.shift.time == "2400h":
            nights_by_pid.setdefault(a.physician_id, []).append(a.date)
    physician_singletons: dict[str, int] = {}
    for pid, dates in nights_by_pid.items():
        dates_sorted = sorted(dates)
        count = sum(
            1 for j, d in enumerate(dates_sorted)
            if not (j > 0 and (d - dates_sorted[j - 1]).days == 1)
            and not (j < len(dates_sorted) - 1 and (dates_sorted[j + 1] - d).days == 1)
        )
        if count:
            physician_singletons[pid] = count

    stats = ScheduleStats(
        total_slots=total,
        filled_slots=filled,
        unfilled_slots=len(unfilled),
        group_a_count=group_a,
        group_b_count=group_b,
        group_a_pct=round(group_a / filled, 3) if filled else 0.0,
        group_b_pct=round(group_b / filled, 3) if filled else 0.0,
        physician_counts=physician_counts,
        physician_singletons=physician_singletons,
        solver_status=None,
        optimality_gap_pct=None,
    )

    return ScheduleResult(
        year=year,
        month=month,
        assignments=assignments,
        unfilled=unfilled,
        issues=[],
        stats=stats,
        on_calls=on_calls,
    )


@app.post("/api/load-schedule", response_model=ScheduleResponse)
def load_schedule(body: LoadScheduleRequest) -> ScheduleResponse:
    """
    Load a previously exported schedule xlsx and make it the active schedule.

    Builds synthetic PhysicianSubmission objects (all days available) so that
    manual assignment and swap endpoints work without re-importing preferences.
    Constraint violations will still be reported as warnings.
    """
    fp = Path(body.file)
    if not fp.is_file():
        raise HTTPException(status_code=400, detail=f"File not found: {body.file}")
    if fp.suffix.lower() != ".xlsx":
        raise HTTPException(status_code=400, detail="File must be an .xlsx file.")

    roster = _state.get("roster") or {}
    if not roster:
        try:
            roster = load_roster()
            _state["roster"] = roster
        except Exception:
            roster = {}

    try:
        result = _parse_schedule_xlsx(fp, roster)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse schedule: {exc}")

    # Validate month consistency with already-imported preferences
    existing_subs: list[PhysicianSubmission] = _state.get("submissions") or []
    if existing_subs:
        sub_year = existing_subs[0].year
        sub_month = existing_subs[0].month
        if (sub_year, sub_month) != (result.year, result.month):
            import calendar as _cal
            sub_mon_name = _cal.month_name[sub_month]
            sched_mon_name = _cal.month_name[result.month]
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Month mismatch: imported preferences are for {sub_mon_name} {sub_year} "
                    f"but the schedule is for {sched_mon_name} {result.year}. "
                    f"Re-import preferences for {sched_mon_name} {result.year} first, "
                    f"or load the schedule without preferences."
                ),
            )

    _state["result"] = result
    _state["year"] = result.year
    _state["month"] = result.month
    _state["generator"] = None   # will be rebuilt on first assign call

    # If no real submissions were imported, build synthetic ones so manual
    # assignment still works (constraint checks will fire as warnings).
    if not existing_subs:
        cfg = _state.get("scheduler_config") or _load_scheduler_config()
        _state["submissions"] = _synthetic_submissions(result, roster)
        _state["scheduler_config"] = cfg

    return _result_to_response(result)


@app.post("/api/assign", response_model=ManualAssignResponse)
def manual_assign(body: ManualAssignRequest) -> ManualAssignResponse:
    """
    Manually assign a physician to a shift slot (human override).
    The assignment is force-applied even if rules are violated.
    Returns the list of rules that were broken for display.
    """
    gen, result = _require_generator()

    if body.shift_code not in ALL_SHIFT_CODES:
        raise HTTPException(status_code=400, detail=f"Unknown shift code: {body.shift_code!r}")

    pid = _resolve_pid(gen, body.physician_id)

    try:
        d = datetime.date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {body.date!r}")

    # Hard block: physician already has a different shift on this day
    same_day = [
        a for a in result.assignments
        if a.physician_id == pid and a.date == d and a.shift.code != body.shift_code
    ]
    if same_day:
        sub_name = gen.submissions[pid].physician_name
        raise HTTPException(
            status_code=400,
            detail=f"{sub_name} already has a shift on {body.date} ({same_day[0].shift.code}). A physician cannot work two shifts on the same day.",
        )

    # Find the Shift object
    shift_obj: Shift | None = None
    for block in BLOCKS:
        for s in block:
            if s.code == body.shift_code:
                shift_obj = s
                break
        if shift_obj:
            break

    # If slot is already occupied, unassign the previous physician first
    existing_assignment = next(
        (a for a in result.assignments if a.date == d and a.shift.code == body.shift_code),
        None,
    )
    if existing_assignment:
        gen._unassign(existing_assignment.physician_id, d, shift_obj)
        result.assignments = [
            a for a in result.assignments
            if not (a.date == d and a.shift.code == body.shift_code)
        ]

    violations = gen.assign_manual(pid, d, shift_obj)

    # Update the result: remove from unfilled if it was there, add to assignments
    sub = gen.submissions[pid]
    result.unfilled = [
        u for u in result.unfilled
        if not (u.date == d and u.shift.code == body.shift_code)
    ]
    result.assignments.append(
        Assignment(
            date=d,
            shift=shift_obj,
            physician_id=body.physician_id,
            physician_name=sub.physician_name,
            is_manual=True,
        )
    )

    # Recalculate stats so the sidebar reflects the updated assignment/unfilled counts.
    # Preserve CP-SAT solver quality fields which _compute_stats doesn't set.
    _old_status = result.stats.solver_status if result.stats else None
    _old_gap = result.stats.optimality_gap_pct if result.stats else None
    result.stats = gen._compute_stats(result)
    if result.stats and _old_status:
        result.stats.solver_status = _old_status
        result.stats.optimality_gap_pct = _old_gap

    return ManualAssignResponse(
        success=True,
        violations=[_v(v) for v in violations],
        message=(
            f"Assigned {sub.physician_name} to {body.shift_code} on {body.date}."
            + (f" {len(violations)} rule(s) overridden." if violations else " No rule violations.")
        ),
    )


@app.post("/api/check-violations")
def check_violations(body: ManualAssignRequest):
    """
    Check rule violations for assigning a physician to a slot WITHOUT assigning.
    Temporarily unassigns any current occupant for an accurate check, then restores.
    """
    gen, result = _require_generator()

    if body.shift_code not in ALL_SHIFT_CODES:
        raise HTTPException(status_code=400, detail=f"Unknown shift code: {body.shift_code!r}")

    pid = _resolve_pid(gen, body.physician_id)

    try:
        d = datetime.date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {body.date!r}")

    shift_obj: Shift | None = None
    for block in BLOCKS:
        for s in block:
            if s.code == body.shift_code:
                shift_obj = s
                break
        if shift_obj:
            break

    # Temporarily unassign current occupant for accurate check
    existing = next(
        (a for a in result.assignments if a.date == d and a.shift.code == body.shift_code),
        None,
    )
    if existing:
        gen._unassign(existing.physician_id, d, shift_obj)

    violations = gen._check_constraints(pid, d, shift_obj) or []

    # Restore the previous occupant
    if existing:
        gen._assign(existing.physician_id, d, shift_obj)

    return {
        "violations": [
            _v(v) for v in violations
        ]
    }


@app.get("/api/candidates", response_model=CandidatesResponse)
def get_candidates(date: str, shift_code: str) -> CandidatesResponse:
    """
    Recalculate fresh candidates for an unfilled slot.

    Unlike the candidates embedded in the schedule response (which are computed
    at generation time and can become stale after manual assignments), this
    endpoint uses the live generator state so it correctly reflects any
    assignments made since generation.

    Hard violations (consecutive_limit, spacing_23h, already_assigned_today,
    etc.) cause a physician to be excluded from the list entirely.
    Only soft violations are returned as warnings.
    """
    gen, result = _require_generator()

    if shift_code not in ALL_SHIFT_CODES:
        raise HTTPException(status_code=400, detail=f"Unknown shift code: {shift_code!r}")

    try:
        d = datetime.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date!r}")

    shift_obj: Shift | None = None
    for block in BLOCKS:
        for s in block:
            if s.code == shift_code:
                shift_obj = s
                break
        if shift_obj:
            break

    if shift_obj is None:
        raise HTTPException(status_code=400, detail=f"Shift not found: {shift_code!r}")

    # Temporarily unassign current occupant so the check is accurate for
    # the "slot is empty" case — restoring afterwards.
    existing = next(
        (a for a in result.assignments if a.date == d and a.shift.code == shift_code),
        None,
    )
    if existing:
        gen._unassign(existing.physician_id, d, shift_obj)

    candidates = gen._near_miss_candidates(d, shift_obj, max_n=20)

    if existing:
        gen._assign(existing.physician_id, d, shift_obj)

    return CandidatesResponse(
        date=date,
        shift_code=shift_code,
        candidates=[
            CandidateSchema(
                physician_id=c.physician_id,
                physician_name=c.physician_name,
                violations=[
                    _v(v)
                    for v in c.violations
                ],
                is_hard_blocked=c.is_hard_blocked,
            )
            for c in candidates
        ],
    )


# ---------------------------------------------------------------------------
# On-call endpoints
# ---------------------------------------------------------------------------

def _oncall_violations(pid: str, d: datetime.date, call_type: str, result) -> list[str]:
    """
    Return human-readable constraint violations for assigning pid to a
    DOC or NOC slot on date d.

    DOC rule: no regular shift the day before.
    NOC rule: no regular shift starting after 1200h on that same day.
    """
    violations: list[str] = []
    prev_day = d - datetime.timedelta(days=1)

    if call_type == "DOC":
        if any(a.physician_id == pid and a.date == prev_day for a in result.assignments):
            violations.append("Has a shift the day before")
    elif call_type == "NOC":
        if any(a.physician_id == pid and a.date == d and a.shift.start_hour > 12
               for a in result.assignments):
            violations.append("Has a shift starting after 1200h")

    return violations


@app.get("/api/oncall-candidates", response_model=OnCallCandidatesResponse)
def get_oncall_candidates(date: str, call_type: str) -> OnCallCandidatesResponse:
    """
    Return all physicians who could fill a DOC or NOC slot, with any
    constraint violations noted as warnings.  All physicians are returned
    (violations don't hard-block — the user can force-assign).
    """
    result: ScheduleResult | None = _state.get("result")
    if result is None:
        raise HTTPException(status_code=400, detail="No schedule in memory.")

    try:
        d = datetime.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date!r}")

    call_type = call_type.upper()
    if call_type not in ("DOC", "NOC"):
        raise HTTPException(status_code=400, detail=f"call_type must be DOC or NOC, got {call_type!r}")

    # Current occupant (if any)
    current = next((oc for oc in result.on_calls if oc.date == d and oc.call_type == call_type), None)

    # Build candidate list from roster + anyone already in the schedule
    roster: dict = _state.get("roster") or {}
    all_pids: set[str] = set(roster.keys()) | {a.physician_id for a in result.assignments}

    candidates: list[OnCallCandidateSchema] = []
    for pid in sorted(all_pids):
        info = roster.get(pid)
        if info and getattr(info, "no_call", False):
            continue   # physician is exempt from on-call
        name = getattr(info, "name", pid) if info else pid
        violations = _oncall_violations(pid, d, call_type, result)
        candidates.append(OnCallCandidateSchema(
            physician_id=pid,
            physician_name=name,
            violations=violations,
        ))

    # Sort: no violations first, then alphabetically by name
    candidates.sort(key=lambda c: (len(c.violations) > 0, c.physician_name))

    return OnCallCandidatesResponse(
        date=date,
        call_type=call_type,
        current_physician_id=current.physician_id if current else None,
        candidates=candidates,
    )


@app.post("/api/assign-oncall", response_model=ScheduleResponse)
def assign_oncall(body: AssignOnCallRequest) -> ScheduleResponse:
    """
    Assign, change, or remove a physician from a DOC or NOC slot.
    Set physician_id to empty string to remove the on-call assignment.
    Violations are warnings only — assignments are force-applied.
    """
    result: ScheduleResult | None = _state.get("result")
    if result is None:
        raise HTTPException(status_code=400, detail="No schedule in memory.")

    try:
        d = datetime.date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {body.date!r}")

    call_type = body.call_type.upper()
    if call_type not in ("DOC", "NOC"):
        raise HTTPException(status_code=400, detail=f"call_type must be DOC or NOC")

    # Remove existing on-call for this date/type
    result.on_calls = [
        oc for oc in result.on_calls
        if not (oc.date == d and oc.call_type == call_type)
    ]

    if body.physician_id.strip():
        pid = body.physician_id.strip()
        roster: dict = _state.get("roster") or {}
        info = roster.get(pid)
        name = getattr(info, "name", pid) if info else pid
        result.on_calls.append(OnCallAssignment(
            date=d,
            call_type=call_type,
            physician_id=pid,
            physician_name=name,
        ))

    return _result_to_response(result)


# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging
    import os
    log_path = Path(os.environ.get("CONFIG_DIR", ".")).parent / "scheduler_debug.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(str(log_path), encoding="utf-8"),
        ],
    )
    # Loopback-only by default — the desktop app only ever needs to reach
    # its own locally-spawned backend, and this keeps it unreachable from
    # the rest of the LAN. BACKEND_HOST=0.0.0.0 (set by the Dockerfile)
    # is required for a container deployment: Docker's -p port mapping
    # forwards to the container's network interface, not its loopback, so
    # a service bound to 127.0.0.1 inside a container is unreachable from
    # outside it no matter how the port is published.
    bind_host = os.environ.get("BACKEND_HOST", "127.0.0.1")
    uvicorn.run(app, host=bind_host, port=5000, log_level="warning", access_log=False)
