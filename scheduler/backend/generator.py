"""
Schedule generator for the emergency physician scheduling system.

Uses a greedy constraint-filtered solver to assign physicians to shift slots
for a given month.  For any slot that cannot be filled automatically, returns
a ranked list of near-miss candidates (with rule violation explanations) for
human resolution via the frontend.
"""

from __future__ import annotations

import calendar
import datetime
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.models import PhysicianSubmission
from scheduler.backend.shifts import (
    BLOCKS,
    SHIFT_TO_BLOCK,
    Shift,
    SiteGroup,
    is_next_shift_ok,
)


# ---------------------------------------------------------------------------
# Output data models
# ---------------------------------------------------------------------------

@dataclass
class Assignment:
    date: datetime.date
    shift: Shift
    physician_id: str
    physician_name: str
    is_manual: bool = False        # True when assigned by a human after generation
    is_claude: bool = False        # True when assigned by the Claude improvement pass


@dataclass
class ViolationReason:
    rule: str
    description: str


@dataclass
class CandidateOption:
    """A physician who could cover an unfilled slot but would violate rules."""
    physician_id: str
    physician_name: str
    violations: list[ViolationReason]
    is_hard_blocked: bool = False   # True when a hard safety/competency rule is violated


# Rules that constitute an absolute hard block — physician must not appear as
# a selectable candidate in the ConflictModal.  Soft rules (weekend_limit,
# anchor_limit, timed_separation, etc.) still show as warnings.
_HARD_VIOLATION_RULES: frozenset[str] = frozenset({
    "already_assigned_today",   # physically impossible: two shifts same day
    "consecutive_limit",        # safety: max consecutive days
    "spacing_23h",              # safety: 23 h minimum gap between shifts
    "post_2400h_rest",          # safety: mandatory rest after overnight
    "shift_type_restriction",   # competency: e.g. only_2400h physicians
    "forbidden_site",           # competency: site they cannot work
    "availability",             # physician said they cannot work this day/block
    "shift_not_available",      # physician did not mark this specific shift
    "max_shifts",               # hard cap on total shifts for the month
    "niar_limit",               # safety: max consecutive overnight (2400h) shifts
})


@dataclass
class UnfilledSlot:
    date: datetime.date
    shift: Shift
    candidates: list[CandidateOption]   # 3-5 options for human resolution


@dataclass
class ScheduleStats:
    total_slots: int
    filled_slots: int
    unfilled_slots: int
    group_a_count: int
    group_b_count: int
    group_a_pct: float
    group_b_pct: float
    physician_counts: dict[str, int]    # physician_id -> shifts assigned
    physician_singletons: dict[str, int]  # physician_id -> isolated 2400h count
    # CP-SAT quality fields (None when greedy solver used)
    solver_status: str | None = None       # "optimal" | "feasible" | None
    optimality_gap_pct: float | None = None  # % gap to proven upper bound; 0.0 = optimal


@dataclass
class OnCallAssignment:
    date: datetime.date
    call_type: str          # "DOC" or "NOC"
    physician_id: str
    physician_name: str


@dataclass
class ScheduleResult:
    year: int
    month: int
    assignments: list[Assignment] = field(default_factory=list)
    unfilled: list[UnfilledSlot] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    stats: Optional[ScheduleStats] = None
    on_calls: list[OnCallAssignment] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_DEFAULT_ANCHOR_MAX = 4
_DEFAULT_MAX_WEEKENDS = 2
_DEFAULT_MAX_CONSEC = 3
_WEEKEND_WEEKDAYS = frozenset({4, 5, 6})   # Friday=4, Saturday=5, Sunday=6


def _weekend_key(d: datetime.date) -> tuple:
    """Return a key identifying the Fri/Sat/Sun cluster containing *d*."""
    offset = d.weekday() - 4      # 0 for Friday, 1 for Saturday, 2 for Sunday
    friday = d - datetime.timedelta(days=offset)
    return (friday.year, friday.month, friday.day)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class ScheduleGenerator:
    def __init__(
        self,
        submissions: list[PhysicianSubmission],
        roster: dict[str, PhysicianConfig],
        config: dict,
    ) -> None:
        self.submissions: dict[str, PhysicianSubmission] = {
            s.physician_id: s for s in submissions
        }
        self.roster = roster
        self.config = config

        # Config shortcuts
        anchor_cfg = config.get("anchor_shifts", {})
        self._anchor_max: int = anchor_cfg.get("anchor_max_total", _DEFAULT_ANCHOR_MAX)
        self._anchor_tol: int = anchor_cfg.get("anchor_target_tolerance", 1)
        self._max_weekends: int = (
            config.get("weekends", {}).get("max_weekends_per_month", _DEFAULT_MAX_WEEKENDS)
        )
        self._max_consec_default: int = (
            config.get("consecutive", {}).get("max_consecutive_shifts", _DEFAULT_MAX_CONSEC)
        )
        self._group_a_target: float = (
            config.get("site_distribution", {}).get("group_a_target", 0.40)
        )
        self._group_a_tolerance: float = (
            config.get("site_distribution", {}).get("tolerance", 0.05)
        )

        # Pair constraints (replaces simple paired_exclusions)
        pairs_cfg = config.get("physician_pairs", {})
        self._timed_separation: list[dict] = (
            config.get("timed_separation", pairs_cfg.get("timed_separation", []))
        )
        self._conditional_cowork: list[dict] = (
            config.get("conditional_cowork", pairs_cfg.get("conditional_cowork", []))
        )

        # Case-insensitive lookups so flat-file names (e.g. "BRAUN") match
        # roster IDs (e.g. "Braun") and config pair names (e.g. "Houston").
        self._roster_lower: dict = {k.lower(): v for k, v in roster.items()}
        # Also index by config .name field so flat-file names like "R Scheirer"
        # resolve to the config keyed by "RScheirer".
        self._roster_by_name: dict = {v.name.lower(): v for v in roster.values()}
        self._pid_lower: dict[str, str] = {pid.lower(): pid for pid in self.submissions}

        # Availability index: (pid, date, block_idx) -> True
        self._avail: set[tuple] = set()
        # Per-day specific shift availability: (pid, date) -> frozenset[shift_code]
        self._shift_avail: dict[tuple, frozenset] = {}
        for sub in submissions:
            for day in sub.days:
                for b in day.available_blocks:
                    self._avail.add((sub.physician_id, day.date, b))
                if day.requested_shifts:
                    self._shift_avail[(sub.physician_id, day.date)] = day.requested_shifts

        # Mutable assignment state (reset each generate() call)
        self._reset_state()

    def _reset_state(self) -> None:
        self._slot_to_pid: dict[tuple, str] = {}           # (date, shift_code) -> pid
        self._pid_to_slots: dict[str, list] = defaultdict(list)  # pid -> [(date, Shift)]
        self._shift_count: dict[str, int] = defaultdict(int)
        self._anchor_count: dict[str, int] = defaultdict(int)
        self._weekend_keys: dict[str, set] = defaultdict(set)

    # -------------------------------------------------------------------
    # Public entry point
    # -------------------------------------------------------------------

    def generate(self, year: int, month: int, seed: int | None = None) -> ScheduleResult:
        self._reset_state()
        rng = random.Random(seed)

        days_in_month = calendar.monthrange(year, month)[1]
        self._days_in_month = days_in_month   # used by _score for pacing
        all_dates = [datetime.date(year, month, d) for d in range(1, days_in_month + 1)]

        def _effective_requested(sub) -> int:
            """Requested shift count, with flex fallback for 0-request physicians."""
            r = sub.shifts_requested
            if r == 0 and sub.shifts_max > 0:
                return min(10, sub.shifts_max)
            return r

        # Block-placement phases: largest consecutive runs first, singletons last.
        # ── Pass A: fill every physician to their requested count ────────────
        # Physicians are sorted each round by descending deficit (how far below
        # their requested count they are) so under-scheduled physicians always
        # get first pick of windows before anyone is filled beyond requested.
        # Dates are reshuffled each block_size to avoid sequential date bias.
        for block_size in range(4, 0, -1):
            shuffled_dates = list(all_dates)
            rng.shuffle(shuffled_dates)
            made_progress = True
            while made_progress:
                made_progress = False
                all_pids = list(self.submissions.keys())
                # Sort by descending deficit, break ties randomly
                all_pids.sort(
                    key=lambda p: (
                        -(max(0, _effective_requested(self.submissions[p]) - self._shift_count[p])),
                        rng.random(),
                    )
                )
                for pid in all_pids:
                    sub = self.submissions[pid]
                    cfg = (self.roster.get(pid)
                           or self._roster_lower.get(pid.lower())
                           or self._roster_by_name.get(pid.lower()))
                    max_consec = cfg.max_consecutive_shifts if cfg else self._max_consec_default
                    if block_size > max_consec:
                        continue
                    # Stop at requested — max fill happens in greedy cleanup
                    req = _effective_requested(sub)
                    if req - self._shift_count[pid] < block_size:
                        continue
                    window = self._find_block_window(pid, shuffled_dates, block_size, rng)
                    if window is not None:
                        for d, shift in window:
                            self._assign(pid, d, shift)
                        made_progress = True

        # ── Deficit-fill pass ────────────────────────────────────────────────
        # Ensure every physician reaches their requested count before the
        # slot-priority cleanup runs.  Uses a multi-round approach (re-sorts
        # by deficit after each assignment) and an exhaustive slot search so
        # that physicians with sparse or constrained availability (e.g. those
        # boxed in by consecutive limits from the block phase) are not missed.
        def _best_eligible_slot(pid: str):
            """Return (date, Shift) with highest score from all unfilled slots,
            or None if the physician has no eligible slot anywhere."""
            best_slot = None
            best_s = float("-inf")
            for d in all_dates:
                for blk in BLOCKS:
                    for sh in blk:
                        if (d, sh.code) in self._slot_to_pid:
                            continue
                        if self._check_constraints(pid, d, sh) is None:
                            s = self._score(pid, d, sh)
                            if s > best_s:
                                best_s = s
                                best_slot = (d, sh)
            return best_slot

        made_deficit_progress = True
        while made_deficit_progress:
            made_deficit_progress = False
            deficit_pids = [
                p for p in self.submissions
                if _effective_requested(self.submissions[p]) - self._shift_count[p] > 0
            ]
            if not deficit_pids:
                break
            deficit_pids.sort(
                key=lambda p: -(
                    _effective_requested(self.submissions[p]) - self._shift_count[p]
                )
            )
            for pid in deficit_pids:
                req = _effective_requested(self.submissions[pid])
                if self._shift_count[pid] >= req:
                    continue
                slot = _best_eligible_slot(pid)
                if slot is not None:
                    self._assign(pid, slot[0], slot[1])
                    made_deficit_progress = True
                    break   # restart loop with refreshed deficit order

        # ── Greedy cleanup pass ──────────────────────────────────────────────
        # Fill any remaining empty slots slot-by-slot.
        # Priority order: 2400h first, then 0600h, then weekends, then rest.
        # Within each tier, hardest slots (fewest eligible physicians) go first.
        # This mirrors the original algorithm and prevents late-month gaps.
        remaining_slots: list[tuple[datetime.date, Shift]] = [
            (d, shift)
            for d in all_dates
            for block in BLOCKS
            for shift in block
            if (d, shift.code) not in self._slot_to_pid
        ]
        remaining_slots.sort(key=lambda s: (
            self._slot_priority(s[0], s[1])[0],   # tier: 2400h=0, 0600h=1, weekend=2, rest=3
            self._slot_difficulty(s[0], s[1]),      # hardest (fewest options) first
            rng.random(),                           # random tiebreak
        ))
        for d, shift in remaining_slots:
            if (d, shift.code) in self._slot_to_pid:
                continue  # already filled by a preceding step
            eligible = self._get_eligible(d, shift)
            if eligible:
                best = max(
                    eligible,
                    key=lambda pid: self._score(pid, d, shift) + rng.uniform(-1.0, 1.0),
                )
                self._assign(best, d, shift)

        # Collect filled and unfilled slots into the result
        result = ScheduleResult(year=year, month=month)
        for d in all_dates:
            for block in BLOCKS:
                for shift in block:
                    pid = self._slot_to_pid.get((d, shift.code))
                    if pid:
                        result.assignments.append(Assignment(
                            date=d, shift=shift,
                            physician_id=pid,
                            physician_name=self.submissions[pid].physician_name,
                        ))
                    else:
                        candidates = self._near_miss_candidates(d, shift, max_n=10)
                        result.unfilled.append(
                            UnfilledSlot(date=d, shift=shift, candidates=candidates)
                        )
                        result.issues.append(
                            f"{d.strftime('%b %d')} {shift.code}: no eligible physician"
                        )

        result.stats = self._compute_stats(result)
        return result

    def _find_block_window(
        self,
        pid: str,
        all_dates: list[datetime.date],
        block_size: int,
        rng: random.Random,
    ) -> list[tuple[datetime.date, Shift]] | None:
        """
        Find *block_size* consecutive calendar days with at least one unfilled,
        eligible shift per day.  Uses trial-assign / undo so consecutive-run
        constraints propagate correctly within the candidate window.
        Returns the chosen (date, Shift) pairs, or None if no window exists.
        """
        n = len(all_dates)
        max_start = n - block_size + 1
        if max_start <= 0:
            return None

        start_indices = list(range(max_start))
        rng.shuffle(start_indices)          # variety across seeds
        for start in start_indices[:30]:    # cap search depth for performance
            window_dates = all_dates[start:start + block_size]
            trial: list[tuple[datetime.date, Shift]] = []
            ok = True
            for d in window_dates:
                eligible: list[Shift] = []
                for blk in BLOCKS:
                    for shift in blk:
                        if (d, shift.code) in self._slot_to_pid:
                            continue    # slot already taken
                        if self._check_constraints(pid, d, shift) is None:
                            eligible.append(shift)
                if not eligible:
                    ok = False
                    break
                best = max(
                    eligible,
                    key=lambda s: self._score(pid, d, s) + rng.uniform(-1.0, 1.0),
                )
                self._assign(pid, d, best)  # tentative — always undone below
                trial.append((d, best))

            # Always undo tentative assignments regardless of outcome
            for d, shift in trial:
                self._unassign(pid, d, shift)

            if ok and len(trial) == block_size:
                return trial

        return None

    def run_best_of(
        self,
        n: int,
        year: int,
        month: int,
        progress_callback=None,
    ) -> ScheduleResult:
        """
        Run generation *n* times with different random seeds and return the
        result that best satisfies all parameters.

        Two-phase Large Neighbourhood Search strategy:
          - Phase 1 (first ceil(n/2) seeds): independent seeds from scratch.
          - Phase 2 (remaining seeds): LNS warm-start — each iteration begins
            from the best Phase-1 result, randomly destroys a subset of
            assignments, then repairs via deficit-fill + greedy cleanup.

        Scoring (higher = better):
          - Primary:   minimise unfilled slots (-1000 each)
          - Secondary: minimise physicians below their minimum shift count (-10 each deficit)
          - Tertiary:  minimise physicians below their requested count (-2 each deficit)
        """
        best: ScheduleResult | None = None
        best_score = float("-inf")

        # Phase 1 gets the extra seed when n is odd (ceiling division).
        phase1_n = (n + 1) // 2
        phase2_n = n - phase1_n

        # ── Phase 1: independent seeds from scratch ──────────────────────────
        for seed in range(phase1_n):
            result = self.generate(year, month, seed=seed)
            s = self._rate_result(result)
            if s > best_score:
                best = result
                best_score = s
            if progress_callback:
                progress_callback(seed + 1, n, best_score)

        # ── Phase 2: LNS warm-start from the best Phase-1 result ─────────────
        if phase2_n > 0 and best is not None:
            lns_rng = random.Random(phase1_n * 1000 + 7)   # deterministic but separate RNG
            for lns_iter in range(phase2_n):
                # Identify physicians adjacent to unfilled slots — always destroy these
                # because their current assignments are blocking the problem slots.
                blocked_pids: set[str] = set()
                if best.unfilled:
                    days_in_month = calendar.monthrange(year, month)[1]
                    all_dates_set = {
                        datetime.date(year, month, d) for d in range(1, days_in_month + 1)
                    }
                    unfilled_dates = {u.date for u in best.unfilled}
                    for asgn in best.assignments:
                        # Adjacent = same day or one day before/after an unfilled slot
                        if (
                            asgn.date in unfilled_dates
                            or (asgn.date + datetime.timedelta(days=1)) in unfilled_dates
                            or (asgn.date - datetime.timedelta(days=1)) in unfilled_dates
                        ):
                            blocked_pids.add(asgn.physician_id)

                # Randomly destroy destroy_k additional physicians
                destroy_k = lns_rng.randint(6, 14)
                all_pids = list(self.submissions.keys())
                lns_rng.shuffle(all_pids)
                destroyed_pids: set[str] = set(blocked_pids)
                for pid in all_pids:
                    if len(destroyed_pids) - len(blocked_pids) >= destroy_k:
                        break
                    if pid not in destroyed_pids:
                        destroyed_pids.add(pid)

                # Restore partial state: reset then re-assign everything except
                # the assignments belonging to destroyed physicians.
                result = self._generate_from_partial(
                    year, month,
                    preserved=[
                        a for a in best.assignments
                        if a.physician_id not in destroyed_pids
                    ],
                    rng=lns_rng,
                )
                s = self._rate_result(result)
                if s > best_score:
                    best = result
                    best_score = s
                current = phase1_n + lns_iter + 1
                if progress_callback:
                    progress_callback(current, n, best_score)

        return best  # type: ignore[return-value]

    def _generate_from_partial(
        self,
        year: int,
        month: int,
        preserved: list,
        rng: random.Random,
    ) -> ScheduleResult:
        """
        Repair a partially-destroyed schedule via deficit-fill + greedy cleanup.

        Resets internal state, re-assigns *preserved* assignments, then runs
        only the deficit-fill pass and greedy cleanup pass on the remaining
        open slots.  Returns a ScheduleResult in the same format as generate().
        """
        self._reset_state()
        days_in_month = calendar.monthrange(year, month)[1]
        self._days_in_month = days_in_month   # used by _score for pacing
        all_dates = [datetime.date(year, month, d) for d in range(1, days_in_month + 1)]

        def _effective_requested(sub) -> int:
            r = sub.shifts_requested
            if r == 0 and sub.shifts_max > 0:
                return min(10, sub.shifts_max)
            return r

        # Re-apply preserved assignments to rebuild partial state
        for asgn in preserved:
            self._assign(asgn.physician_id, asgn.date, asgn.shift)

        # ── Deficit-fill pass (same logic as generate()) ─────────────────────
        def _best_eligible_slot(pid: str):
            best_slot = None
            best_s = float("-inf")
            for d in all_dates:
                for blk in BLOCKS:
                    for sh in blk:
                        if (d, sh.code) in self._slot_to_pid:
                            continue
                        if self._check_constraints(pid, d, sh) is None:
                            s = self._score(pid, d, sh)
                            if s > best_s:
                                best_s = s
                                best_slot = (d, sh)
            return best_slot

        made_deficit_progress = True
        while made_deficit_progress:
            made_deficit_progress = False
            deficit_pids = [
                p for p in self.submissions
                if _effective_requested(self.submissions[p]) - self._shift_count[p] > 0
            ]
            if not deficit_pids:
                break
            deficit_pids.sort(
                key=lambda p: -(
                    _effective_requested(self.submissions[p]) - self._shift_count[p]
                )
            )
            for pid in deficit_pids:
                req = _effective_requested(self.submissions[pid])
                if self._shift_count[pid] >= req:
                    continue
                slot = _best_eligible_slot(pid)
                if slot is not None:
                    self._assign(pid, slot[0], slot[1])
                    made_deficit_progress = True
                    break   # restart loop with refreshed deficit order

        # ── Greedy cleanup pass ───────────────────────────────────────────────
        remaining_slots: list[tuple[datetime.date, Shift]] = [
            (d, shift)
            for d in all_dates
            for block in BLOCKS
            for shift in block
            if (d, shift.code) not in self._slot_to_pid
        ]
        remaining_slots.sort(key=lambda s: (
            self._slot_priority(s[0], s[1])[0],
            self._slot_difficulty(s[0], s[1]),
            rng.random(),
        ))
        for d, shift in remaining_slots:
            if (d, shift.code) in self._slot_to_pid:
                continue
            eligible = self._get_eligible(d, shift)
            if eligible:
                best_pid = max(
                    eligible,
                    key=lambda pid: self._score(pid, d, shift) + rng.uniform(-1.0, 1.0),
                )
                self._assign(best_pid, d, shift)

        # Collect results
        result = ScheduleResult(year=year, month=month)
        for d in all_dates:
            for block in BLOCKS:
                for shift in block:
                    pid = self._slot_to_pid.get((d, shift.code))
                    if pid:
                        result.assignments.append(Assignment(
                            date=d, shift=shift,
                            physician_id=pid,
                            physician_name=self.submissions[pid].physician_name,
                        ))
                    else:
                        candidates = self._near_miss_candidates(d, shift, max_n=10)
                        result.unfilled.append(
                            UnfilledSlot(date=d, shift=shift, candidates=candidates)
                        )
                        result.issues.append(
                            f"{d.strftime('%b %d')} {shift.code}: no eligible physician"
                        )

        result.stats = self._compute_stats(result)
        return result

    def _rate_result(self, result: ScheduleResult) -> float:
        score = -len(result.unfilled) * 1000.0
        counts = result.stats.physician_counts if result.stats else {}
        singletons = result.stats.physician_singletons if result.stats else {}
        for pid, sub in self.submissions.items():
            assigned = counts.get(pid, 0)
            effective_requested = sub.shifts_requested
            if effective_requested == 0 and sub.shifts_max > 0:
                effective_requested = min(10, sub.shifts_max)
            effective_min = sub.shifts_min if sub.shifts_min > 0 else effective_requested
            min_deficit = max(0, effective_min - assigned)
            req_deficit = max(0, effective_requested - assigned)
            # Low-request physicians (≤5) getting unfilled minimums is a worse
            # outcome — penalise more heavily so the best-of loop prioritises them.
            min_weight = 20.0 if sub.shifts_requested <= 5 else 10.0
            score -= min_deficit * min_weight
            score -= req_deficit * 2.0
        # Penalise singleton 2400h assignments (strong: -5 per singleton)
        # Skip physicians who prefer singleton nights.
        for pid, n in singletons.items():
            cfg = self.roster.get(pid)
            if cfg and cfg.prefer_singleton_nights:
                continue
            score -= n * 5.0
        # Penalise per-physician Group A/B imbalance
        for pid, sub in self.submissions.items():
            slots = self._pid_to_slots[pid]
            if not slots:
                continue
            a_count = sum(1 for _, s in slots if s.site_group == SiteGroup.A)
            frac_a = a_count / len(slots)
            imbalance = abs(frac_a - self._group_a_target)
            score -= imbalance * len(slots) * 3.0
        return score

    # -------------------------------------------------------------------
    # Manual assignment (called by the frontend after human resolution)
    # -------------------------------------------------------------------

    def assign_manual(
        self,
        physician_id: str,
        d: datetime.date,
        shift: Shift,
    ) -> list[ViolationReason]:
        """
        Force-assign *physician_id* to (*d*, *shift*), ignoring rule violations.
        Returns the list of rules that were broken (for the frontend to display).
        """
        violations = self._check_constraints(physician_id, d, shift) or []
        self._assign(physician_id, d, shift)
        return violations

    # -------------------------------------------------------------------
    # State management
    # -------------------------------------------------------------------

    def _assign(self, pid: str, d: datetime.date, shift: Shift) -> None:
        self._slot_to_pid[(d, shift.code)] = pid
        self._pid_to_slots[pid].append((d, shift))
        self._shift_count[pid] += 1
        if shift.time in ("0600h", "2400h"):
            self._anchor_count[pid] += 1
        if d.weekday() in _WEEKEND_WEEKDAYS:
            self._weekend_keys[pid].add(_weekend_key(d))

    def _unassign(self, pid: str, d: datetime.date, shift: Shift) -> None:
        self._slot_to_pid.pop((d, shift.code), None)
        self._pid_to_slots[pid] = [(ad, s) for ad, s in self._pid_to_slots[pid]
                                    if not (ad == d and s.code == shift.code)]
        self._shift_count[pid] = max(0, self._shift_count[pid] - 1)
        if shift.time in ("0600h", "2400h"):
            self._anchor_count[pid] = max(0, self._anchor_count[pid] - 1)
        # Recompute weekend keys from scratch (simpler than tracking removals)
        if d.weekday() in _WEEKEND_WEEKDAYS:
            self._weekend_keys[pid] = {
                _weekend_key(ad) for ad, _ in self._pid_to_slots[pid]
                if ad.weekday() in _WEEKEND_WEEKDAYS
            }

    # -------------------------------------------------------------------
    # Eligibility and constraint checking
    # -------------------------------------------------------------------

    def _slot_priority(self, d: datetime.date, shift: Shift) -> tuple:
        """Return (tier, difficulty) — lower sorts first."""
        if shift.time == "2400h":
            tier = 0
        elif shift.time == "0600h":
            tier = 1
        elif d.weekday() in _WEEKEND_WEEKDAYS:
            tier = 2
        else:
            tier = 3
        return (tier, self._slot_difficulty(d, shift))

    def _slot_difficulty(self, d: datetime.date, shift: Shift) -> int:
        block_idx = SHIFT_TO_BLOCK[shift.code]
        return sum(
            1 for pid in self.submissions
            if (pid, d, block_idx) in self._avail
            and shift.site not in self._forbidden(pid)
        )

    def _get_eligible(self, d: datetime.date, shift: Shift) -> list[str]:
        block_idx = SHIFT_TO_BLOCK[shift.code]
        return [
            pid for pid in self.submissions
            if self._check_constraints(pid, d, shift, block_idx) is None
        ]

    def _check_constraints(
        self,
        pid: str,
        d: datetime.date,
        shift: Shift,
        block_idx: int | None = None,
    ) -> list[ViolationReason] | None:
        """Return None if all hard constraints pass, or a list of violations."""
        if block_idx is None:
            block_idx = SHIFT_TO_BLOCK[shift.code]

        sub = self.submissions[pid]
        cfg = (self.roster.get(pid)
               or self._roster_lower.get(pid.lower())
               or self._roster_by_name.get(pid.lower()))
        v: list[ViolationReason] = []

        # 1. Block availability
        if (pid, d, block_idx) not in self._avail:
            v.append(ViolationReason(
                rule="availability",
                description=f"Not available for block {block_idx} on {d}",
            ))

        # 1b. Specific shift availability — if the physician listed individual
        #     shifts (flat-file format), only assign shifts they actually marked.
        day_shifts = self._shift_avail.get((pid, d))
        if day_shifts is not None and shift.code not in day_shifts:
            v.append(ViolationReason(
                rule="shift_not_available",
                description=f"Only available for: {', '.join(sorted(day_shifts))}",
            ))

        # 2. Forbidden site
        if shift.site in self._forbidden(pid):
            v.append(ViolationReason(
                rule="forbidden_site",
                description=f"{shift.site} is a forbidden site for this physician",
            ))

        # 2b. Shift-type restriction: only_2400h physicians may only work 2400h shifts.
        if cfg and cfg.only_2400h and shift.time != "2400h":
            v.append(ViolationReason(
                rule="shift_type_restriction",
                description=(
                    f"{cfg.name} is restricted to 2400h shifts only "
                    f"(requested: {shift.time})"
                ),
            ))

        # 2c. Forbidden shift times (e.g. no 0600h or 2400h)
        if cfg and shift.time in cfg.forbidden_shift_times:
            v.append(ViolationReason(
                rule="shift_type_restriction",
                description=(
                    f"{cfg.name} cannot work {shift.time} shifts"
                ),
            ))

        # 3. Already assigned on this calendar day
        if any(ad == d for ad, _ in self._pid_to_slots[pid]):
            v.append(ViolationReason(
                rule="already_assigned_today",
                description=f"Already has a shift assigned on {d}",
            ))

        # 4. Max shifts hard cap (cap_at_requested overrides shifts_max)
        if cfg and cfg.cap_at_requested and sub.shifts_requested > 0:
            hard_max = sub.shifts_requested
        else:
            hard_max = sub.shifts_max if sub.shifts_max > 0 else sub.shifts_requested
        if self._shift_count[pid] >= hard_max:
            v.append(ViolationReason(
                rule="max_shifts",
                description=f"Already at maximum shifts ({hard_max})",
            ))

        # 5. Consecutive day limit — bidirectional so month-wide scheduling
        # (which processes dates out of order) cannot produce illegal runs.
        max_consec = cfg.max_consecutive_shifts if cfg else self._max_consec_default
        run_before = self._run_length_ending_before(pid, d)
        run_after  = self._run_length_starting_after(pid, d)
        total_run  = run_before + 1 + run_after
        if total_run > max_consec:
            v.append(ViolationReason(
                rule="consecutive_limit",
                description=(
                    f"Would create a {total_run}-day consecutive run "
                    f"(limit {max_consec})"
                ),
            ))

        # 6. Spacing: 22h minimum / post-2400h rest rule — checked bidirectionally
        # so that month-wide scheduling (later dates processed first) never creates
        # illegal turnarounds regardless of processing order.
        prev = self._prev_assigned(pid, d)
        if prev:
            prev_shift, prev_date = prev
            gap = (d - prev_date).days
            if not is_next_shift_ok(prev_shift, gap, shift):
                if prev_shift.time == "2400h" and gap == 2:
                    v.append(ViolationReason(
                        rule="post_2400h_rest",
                        description=(
                            f"After 2400h on {prev_date:%b %d}, next shift must start "
                            f"at noon or later (requested: {shift.time})"
                        ),
                    ))
                else:
                    actual_h = (shift.start_hour + gap * 24) - prev_shift.start_hour
                    v.append(ViolationReason(
                        rule="spacing_23h",
                        description=(
                            f"Only {actual_h}h gap: {prev_shift.time} on "
                            f"{prev_date:%b %d} → {shift.time} on {d:%b %d} "
                            f"(need 22h)"
                        ),
                    ))
        # Forward check: if a later shift is already assigned, ensure this new
        # shift doesn't violate spacing with it (catches out-of-order processing).
        nxt = self._next_assigned(pid, d)
        if nxt:
            nxt_shift, nxt_date = nxt
            fwd_gap = (nxt_date - d).days
            if not is_next_shift_ok(shift, fwd_gap, nxt_shift):
                if shift.time == "2400h" and fwd_gap == 2:
                    v.append(ViolationReason(
                        rule="post_2400h_rest",
                        description=(
                            f"After 2400h on {d:%b %d}, next shift must start "
                            f"at noon or later (have: {nxt_shift.time} on {nxt_date:%b %d})"
                        ),
                    ))
                else:
                    actual_h = (nxt_shift.start_hour + fwd_gap * 24) - shift.start_hour
                    v.append(ViolationReason(
                        rule="spacing_23h",
                        description=(
                            f"Only {actual_h}h gap: {shift.time} on "
                            f"{d:%b %d} → {nxt_shift.time} on {nxt_date:%b %d} "
                            f"(need 22h)"
                        ),
                    ))

        # 7. Same shift on an adjacent day — bidirectional check so it works
        # regardless of which date was processed first by the scheduler.
        assigned_same_shift = {
            ad for ad, s in self._pid_to_slots[pid] if s.code == shift.code
        }
        if (
            (d - datetime.timedelta(days=1)) in assigned_same_shift
            or (d + datetime.timedelta(days=1)) in assigned_same_shift
        ):
            v.append(ViolationReason(
                rule="same_shift_consecutive",
                description=f"Identical shift ({shift.code}) on an adjacent day",
            ))

        # 8. Anchor shift limit (0600h + 2400h combined, or per-type if specified)
        if shift.time in ("0600h", "2400h"):
            limit = self._anchor_limit(pid, sub, shift)
            if self._anchor_count[pid] >= limit:
                v.append(ViolationReason(
                    rule="anchor_limit",
                    description=(
                        f"Anchor shift count ({self._anchor_count[pid]}) already "
                        f"at limit ({limit})"
                    ),
                ))

        # 9. Weekend limit (hard block — frontend may override with confirmation)
        if d.weekday() in _WEEKEND_WEEKDAYS:
            wk = _weekend_key(d)
            eff_max_weekends = (
                cfg.max_weekends
                if cfg and cfg.max_weekends is not None
                else self._max_weekends
            )
            if (
                len(self._weekend_keys[pid]) >= eff_max_weekends
                and wk not in self._weekend_keys[pid]
            ):
                v.append(ViolationReason(
                    rule="weekend_limit",
                    description=(
                        f"Would occupy weekend #{len(self._weekend_keys[pid]) + 1} "
                        f"(limit {eff_max_weekends} — requires confirmation)"
                    ),
                ))

        # 10. Timed separation (Brenneis/Fanaeian: ≥6h gap, no 1700/1800 + 2400)
        for rule in self._timed_separation:
            phys = [self._resolve_pid(p) for p in rule.get("physicians", [])]
            phys = [p for p in phys if p]
            if pid in phys:
                other = phys[0] if phys[1] == pid else phys[1]
                other_shift = self._shift_on_date(other, d)
                if other_shift:
                    from scheduler.backend.shifts import _START_HOURS
                    gap_h = abs(
                        _START_HOURS.get(shift.time, 0)
                        - _START_HOURS.get(other_shift.time, 0)
                    )
                    min_gap = rule.get("min_hours_gap", 0)
                    if gap_h < min_gap:
                        v.append(ViolationReason(
                            rule="timed_separation",
                            description=(
                                f"{other} works {other_shift.time} on {d}; "
                                f"gap to {shift.time} is {gap_h}h < {min_gap}h required"
                            ),
                        ))
                    for pair_times in rule.get("forbidden_time_pairs", []):
                        if shift.time in pair_times and other_shift.time in pair_times:
                            v.append(ViolationReason(
                                rule="forbidden_time_pair",
                                description=(
                                    f"Forbidden combination: {shift.time} + "
                                    f"{other_shift.time} on same day with {other}"
                                ),
                            ))

        # 10b. Conditional co-working (Edgecumbe/Houston)
        for rule in self._conditional_cowork:
            phys = [self._resolve_pid(p) for p in rule.get("physicians", [])]
            phys = [p for p in phys if p]
            if pid in phys:
                other = phys[0] if phys[1] == pid else phys[1]
                other_shift = self._shift_on_date(other, d)
                if other_shift:
                    if rule.get("no_shared_weekends") and d.weekday() in _WEEKEND_WEEKDAYS:
                        v.append(ViolationReason(
                            rule="no_shared_weekend",
                            description=(
                                f"{other} is also working on weekend day {d}; "
                                f"these physicians cannot share a weekend"
                            ),
                        ))
                    required_time = rule.get("weekday_requires_one_at")
                    if required_time and d.weekday() not in _WEEKEND_WEEKDAYS:
                        neither_at = shift.time != required_time and other_shift.time != required_time
                        both_at = shift.time == required_time and other_shift.time == required_time
                        if neither_at or both_at:
                            v.append(ViolationReason(
                                rule="cowork_condition",
                                description=(
                                    f"When {pid} and {other} work the same weekday, "
                                    f"exactly one must be on {required_time} "
                                    f"({'neither' if neither_at else 'both'} are)"
                                ),
                            ))

        # 11b. Rest after late shift (1600h/1800h/2000h → next day off)
        _LATE_TIMES = {"1600h", "1800h", "2000h"}
        if cfg and cfg.rest_after_late_shift:
            # Check: previous day has a late shift → today is blocked
            prev = self._prev_assigned(pid, d)
            if prev:
                prev_shift, prev_date = prev
                if (d - prev_date).days == 1 and prev_shift.time in _LATE_TIMES:
                    v.append(ViolationReason(
                        rule="rest_after_late_shift",
                        description=(
                            f"Must rest day after {prev_shift.time} shift on {prev_date:%b %d}"
                        ),
                    ))
            # Check: today has a late shift → next assigned day blocked
            if shift.time in _LATE_TIMES:
                nxt = self._next_assigned(pid, d)
                if nxt:
                    nxt_shift, nxt_date = nxt
                    if (nxt_date - d).days == 1:
                        v.append(ViolationReason(
                            rule="rest_after_late_shift",
                            description=(
                                f"Late shift ({shift.time}) would block rest before "
                                f"{nxt_shift.time} on {nxt_date:%b %d}"
                            ),
                        ))

        # 11c. Max consecutive 1800h shifts
        if cfg and cfg.max_consecutive_1800h < 3 and shift.time == "1800h":
            mc1800 = cfg.max_consecutive_1800h
            # Count consecutive 1800h run ending on the day before d
            run = 0
            check = d - datetime.timedelta(days=1)
            while True:
                s = self._shift_on_date(pid, check)
                if s and s.time == "1800h":
                    run += 1
                    check -= datetime.timedelta(days=1)
                else:
                    break
            if run >= mc1800:
                v.append(ViolationReason(
                    rule="consecutive_1800h_limit",
                    description=(
                        f"Would extend consecutive 1800h run to {run + 1} "
                        f"(limit {mc1800})"
                    ),
                ))

        # 11. NIAR — max consecutive 2400h shifts
        if shift.time == "2400h":
            cfg2 = (self.roster.get(pid)
                    or self._roster_lower.get(pid.lower())
                    or self._roster_by_name.get(pid.lower()))
            max_nights = (
                cfg2.max_consecutive_nights if cfg2 else self._max_consec_default
            )
            night_run = self._night_run_ending_before(pid, d)
            if night_run >= max_nights:
                v.append(ViolationReason(
                    rule="niar_limit",
                    description=(
                        f"Would extend consecutive night run to {night_run + 1} "
                        f"(NIAR limit {max_nights})"
                    ),
                ))

        return v if v else None

    # -------------------------------------------------------------------
    # Soft scoring
    # -------------------------------------------------------------------

    def _score(self, pid: str, d: datetime.date, shift: Shift) -> float:
        sub = self.submissions[pid]
        cfg = (self.roster.get(pid)
               or self._roster_lower.get(pid.lower())
               or self._roster_by_name.get(pid.lower()))
        score = 0.0

        # +5: physician wants to work this day (Z row)
        for day_avail in sub.days:
            if day_avail.date == d and day_avail.wants_to_work:
                score += 5.0
                break

        # +3: shift site matches within-Group-B preference
        if cfg and cfg.group_b_site_preference and shift.site_group == SiteGroup.B:
            pref = cfg.group_b_site_preference
            if pref == "nehc" and shift.site == "NEHC":
                score += 3.0
            elif pref == "rah" and shift.site in ("RAH I side", "RAH F side"):
                score += 3.0
            elif pref == "rah_f" and shift.site == "RAH F side":
                score += 3.0

        # Group A/B balance: symmetric push toward the 40/60 target.
        # deviation > 0 means too many A shifts; deviation < 0 means too few A shifts.
        # For an A shift: reward when below target, penalise when above.
        # For a B shift: reward when above target (corrects A-heavy), penalise when below.
        # Using a single signed formula keeps the logic consistent and the
        # coefficient (60) large enough to override most other soft factors.
        current_balance = self._group_a_fraction(pid)
        deviation = current_balance - self._group_a_target
        if shift.site_group == SiteGroup.A:
            score -= deviation * 80.0   # negative deviation (need more A) → bonus
        elif shift.site_group == SiteGroup.B:
            score += deviation * 80.0   # positive deviation (too many A) → bonus for B

        # Priority by unmet minimum/requested, normalised so low-request physicians
        # are not drowned out by high-request ones.
        # Physicians with shifts_requested == 0 but non-zero shifts_max act as
        # flex fill-ins: default to a target of min(10, shifts_max) so they get
        # priority scoring up to that target rather than being ignored entirely.
        assigned = self._shift_count[pid]
        effective_requested = sub.shifts_requested
        if effective_requested == 0 and sub.shifts_max > 0:
            effective_requested = min(10, sub.shifts_max)
        effective_min = sub.shifts_min if sub.shifts_min > 0 else (effective_requested if effective_requested > 0 else 0)

        min_deficit = max(0, effective_min - assigned)
        req_deficit = max(0, effective_requested - assigned)

        # Fractional deficit: how far below minimum/requested as a proportion (0–1).
        # A physician at 0/3 (100% unmet) scores the same as one at 0/12 (100%
        # unmet), so small requestors are not crowded out by higher-volume ones.
        frac_min_deficit = min_deficit / max(effective_min, 1)
        frac_req_deficit = req_deficit / max(effective_requested, 1)
        score += frac_min_deficit * 30.0   # strong: unmet minimum dominates
        score += frac_req_deficit * 10.0   # moderate: below requested

        # Absolute deficit bonus: each shift still owed adds a flat bonus so that
        # a physician 3 shifts below their minimum always outbids someone who is
        # already at or above their requested count, regardless of other factors.
        score += min_deficit * 8.0
        score += req_deficit * 3.0

        # Extra protection for physicians requesting ≤5 shifts.
        if sub.shifts_requested <= 5 and min_deficit > 0:
            score += 12.0
            # Honour specific 2400h / 0600h requests for these physicians.
            night_unmet = max(0, sub.shifts_2400h_requested - self._anchor_count[pid])
            am_unmet    = max(0, sub.shifts_0600h_requested - self._anchor_count[pid])
            if shift.time == "2400h" and night_unmet > 0:
                score += 5.0
            if shift.time == "0600h" and am_unmet > 0:
                score += 5.0

        # Scarcity bonus: physicians with very few available days for this block
        # get priority so they aren't squeezed out by more flexible physicians.
        block_idx_score = SHIFT_TO_BLOCK[shift.code]
        avail_count = sum(
            1 for day in sub.days
            if (pid, day.date, block_idx_score) in self._avail
        )
        if 0 < avail_count <= 8:
            score += (9 - avail_count) * 0.4

        # Strong penalty: avoid consecutive Group-A assignments in sequence.
        # Allowed patterns: A-B, B-A, A-B-B, B-B-A, B-A-B.  Penalise A-A heavily
        # so it is only chosen when no alternative exists (not a hard block, which
        # caused unfilled slots due to month-wide out-of-order scheduling).
        if shift.site_group == SiteGroup.A:
            prev_aa = self._prev_assigned(pid, d)
            nxt_aa = self._next_assigned(pid, d)
            if prev_aa and prev_aa[0].site_group == SiteGroup.A:
                score -= 30.0
            if nxt_aa and nxt_aa[0].site_group == SiteGroup.A:
                score -= 30.0

        # Soft penalty: avoid back-to-back RAH I side shifts on adjacent days.
        # RAH I is a high-intensity site; consecutive days there are undesirable.
        if shift.site == "RAH I side":
            rah_i_dates = {ad for ad, s in self._pid_to_slots[pid]
                           if s.site == "RAH I side"}
            if ((d - datetime.timedelta(days=1)) in rah_i_dates
                    or (d + datetime.timedelta(days=1)) in rah_i_dates):
                score -= 2.5

        # +1: adjacent to an existing run (avoids new singletons)
        if self._has_adjacent(pid, d):
            score += 1.0
        else:
            score -= 0.5    # slight penalty for potential singleton

        # Per-physician soft preferences loaded from config.
        if cfg:
            # prefer_weekends: bonus for weekend days (Fri/Sat/Sun)
            if cfg.prefer_weekends and d.weekday() in _WEEKEND_WEEKDAYS:
                score += 4.0

            # prefer consecutive runs: extra bonus for extending an existing run
            if self._has_adjacent(pid, d):
                score += 3.0

            # honor_all_requests: very high bonus when physician requested this exact shift
            if cfg.honor_all_requests:
                day_shifts = self._shift_avail.get((pid, d))
                if day_shifts is not None and shift.code in day_shifts:
                    score += 50.0

        # 2400h clustering: strongly prefer extending an existing night run over
        # creating a new isolated night.  +6 for continuing a run, -5 for a new
        # isolated night when the physician already has ≥1 night assigned.
        if shift.time == "2400h":
            night_dates_pid = {ad for ad, s in self._pid_to_slots[pid] if s.time == "2400h"}
            prev_night = (d - datetime.timedelta(days=1)) in night_dates_pid
            next_night = (d + datetime.timedelta(days=1)) in night_dates_pid
            if prev_night or next_night:
                score += 6.0   # extends an existing run
            elif night_dates_pid:
                score -= 5.0   # would add an isolated night when runs already exist

        # +1: already working this weekend (avoids spreading to a third)
        if d.weekday() in _WEEKEND_WEEKDAYS:
            if _weekend_key(d) in self._weekend_keys[pid]:
                score += 1.0

        # -1: anchor near limit
        if shift.time in ("0600h", "2400h"):
            limit = self._anchor_limit(pid, sub, shift)
            if self._anchor_count[pid] >= limit - 1:
                score -= 1.0

        # Pacing: penalise assigning a physician who is running significantly
        # ahead of their expected pace through the month.  This distributes
        # assignments across the full month instead of front-loading.
        if hasattr(self, '_days_in_month') and self._days_in_month > 0:
            expected_by_now = effective_requested * (d.day / self._days_in_month)
            overpace = assigned - (expected_by_now + 1.5)
            if overpace > 0:
                score -= overpace * 2.5

        return score

    # -------------------------------------------------------------------
    # Near-miss candidates for unfilled slots
    # -------------------------------------------------------------------

    def _near_miss_candidates(
        self, d: datetime.date, shift: Shift, max_n: int = 10
    ) -> list[CandidateOption]:
        """
        Return up to max_n candidate physicians for a human to consider when
        manually filling an unfilled slot.

        Hard constraints (safety / competency) must BLOCK the physician from
        the list entirely — they are never shown as an option.
        Soft constraints are returned as warnings the human can choose to accept.

        Hard-blocked rules: already_assigned_today, consecutive_limit,
        spacing_23h, post_2400h_rest, forbidden_site, shift_type_restriction,
        availability, shift_not_available, max_shifts, niar_limit.
        """
        block_idx = SHIFT_TO_BLOCK[shift.code]
        results = []
        for pid, sub in self.submissions.items():
            # Hard block: never offer someone already working this day — the
            # constraint check below also catches this, but filtering here is
            # an explicit fast-path so stale candidates data cannot sneak through.
            if any(ad == d for ad, _ in self._pid_to_slots[pid]):
                continue
            # Hard block: never offer someone who marked themselves unavailable.
            day_avail = next((day for day in sub.days if day.date == d), None)
            if day_avail is None or not day_avail.wants_to_work:
                continue

            violations = self._check_constraints(pid, d, shift, block_idx) or []

            # Partition violations into hard and soft.
            hard_viols = [v for v in violations if v.rule in _HARD_VIOLATION_RULES]
            soft_viols = [v for v in violations if v.rule not in _HARD_VIOLATION_RULES]

            # Physicians with any hard violation are excluded entirely.
            if hard_viols:
                continue

            fit = self._near_miss_score(pid, d, shift)
            deficit = max(0, sub.shifts_requested - self._shift_count[pid])
            # Sort key: fewest soft violations first, then best fit, then most deficit
            results.append((len(soft_viols), -fit, -deficit, pid, soft_viols))

        results.sort()
        # Return up to max_n — fewer is fine if fewer physicians qualify
        return [
            CandidateOption(
                physician_id=pid,
                physician_name=self.submissions[pid].physician_name,
                violations=soft_viols,
                is_hard_blocked=False,
            )
            for _, _, _, pid, soft_viols in results[:max_n]
        ]

    def _near_miss_score(self, pid: str, d: datetime.date, shift: Shift) -> float:
        sub = self.submissions[pid]
        block_idx = SHIFT_TO_BLOCK[shift.code]
        score = 0.0
        if (pid, d, block_idx) in self._avail:
            score += 3.0
        if shift.site not in self._forbidden(pid):
            score += 2.0
        if self._shift_count[pid] < sub.shifts_requested:
            score += 1.0
        return score

    # -------------------------------------------------------------------
    # State query helpers
    # -------------------------------------------------------------------

    def _resolve_pid(self, config_pid: str) -> str | None:
        """Map a config physician name to a submission physician_id (case-insensitive)."""
        if config_pid in self.submissions:
            return config_pid
        return self._pid_lower.get(config_pid.lower())

    def _forbidden(self, pid: str) -> list[str]:
        cfg = (self.roster.get(pid)
               or self._roster_lower.get(pid.lower())
               or self._roster_by_name.get(pid.lower()))
        return cfg.forbidden_sites if cfg else []

    def _prev_assigned(
        self, pid: str, before_date: datetime.date
    ) -> tuple[Shift, datetime.date] | None:
        past = [(d, s) for d, s in self._pid_to_slots[pid] if d < before_date]
        if not past:
            return None
        latest_d, latest_s = max(past, key=lambda x: x[0])
        return latest_s, latest_d

    def _next_assigned(
        self, pid: str, after_date: datetime.date
    ) -> tuple[Shift, datetime.date] | None:
        """Return the earliest assigned (shift, date) strictly after after_date."""
        future = [(d, s) for d, s in self._pid_to_slots[pid] if d > after_date]
        if not future:
            return None
        earliest_d, earliest_s = min(future, key=lambda x: x[0])
        return earliest_s, earliest_d

    def _run_length_ending_before(self, pid: str, new_date: datetime.date) -> int:
        """Count how many consecutive days immediately before new_date are assigned."""
        assigned = {d for d, _ in self._pid_to_slots[pid]}
        count = 0
        check = new_date - datetime.timedelta(days=1)
        while check in assigned:
            count += 1
            check -= datetime.timedelta(days=1)
        return count

    def _run_length_starting_after(self, pid: str, new_date: datetime.date) -> int:
        """Count how many consecutive days immediately after new_date are assigned."""
        assigned = {d for d, _ in self._pid_to_slots[pid]}
        count = 0
        check = new_date + datetime.timedelta(days=1)
        while check in assigned:
            count += 1
            check += datetime.timedelta(days=1)
        return count

    def _assigned_at_time(self, pid: str, d: datetime.date, time_str: str) -> bool:
        return any(
            ad == d and s.time == time_str
            for ad, s in self._pid_to_slots[pid]
        )

    def _shift_on_date(self, pid: str, d: datetime.date) -> Shift | None:
        """Return the Shift assigned to pid on date d, or None."""
        for ad, s in self._pid_to_slots[pid]:
            if ad == d:
                return s
        return None

    def _night_run_ending_before(self, pid: str, d: datetime.date) -> int:
        """Count consecutive 2400h shifts assigned immediately before d."""
        night_dates = {ad for ad, s in self._pid_to_slots[pid] if s.time == "2400h"}
        count = 0
        check = d - datetime.timedelta(days=1)
        while check in night_dates:
            count += 1
            check -= datetime.timedelta(days=1)
        return count

    def _has_adjacent(self, pid: str, d: datetime.date) -> bool:
        assigned = {a for a, _ in self._pid_to_slots[pid]}
        return (
            (d - datetime.timedelta(days=1)) in assigned
            or (d + datetime.timedelta(days=1)) in assigned
        )

    def _group_a_fraction(self, pid: str) -> float:
        slots = self._pid_to_slots[pid]
        if not slots:
            return self._group_a_target   # neutral: treat as on-target
        return sum(1 for _, s in slots if s.site_group == SiteGroup.A) / len(slots)

    def _anchor_limit(
        self, pid: str, sub: PhysicianSubmission, shift: Shift
    ) -> int:
        if shift.time == "2400h" and sub.shifts_2400h_requested > 0:
            return sub.shifts_2400h_requested + self._anchor_tol
        if shift.time == "0600h" and sub.shifts_0600h_requested > 0:
            return sub.shifts_0600h_requested + self._anchor_tol
        return self._anchor_max

    # -------------------------------------------------------------------
    # Post-solve repair pass
    # -------------------------------------------------------------------

    def _restore_state_from_result(self, result: ScheduleResult) -> None:
        """Rebuild mutable assignment state from a completed ScheduleResult."""
        self._reset_state()
        for a in result.assignments:
            self._assign(a.physician_id, a.date, a.shift)

    def repair_pass(
        self,
        result: ScheduleResult,
        n_attempts: int = 50,
        seed: int = 42,
    ) -> ScheduleResult:
        """
        Try to fill remaining unfilled slots by juggling adjacent assignments.

        For each unfilled slot, look at physicians assigned 1–2 days away.
        If a neighbour physician can be moved to cover the unfilled slot, and
        their vacated slot can then be covered by someone else, make the swap.
        Run up to n_attempts random permutations per unfilled slot.
        """
        self._restore_state_from_result(result)
        rng = random.Random(seed)

        still_unfilled: list[UnfilledSlot] = []
        # Process a copy so we can iterate while modifying result.assignments
        unfilled_list = list(result.unfilled)

        for unfilled in unfilled_list:
            d, shift = unfilled.date, unfilled.shift
            filled = False

            # Collect candidates: physicians assigned on ±1 or ±2 days
            neighbor_days = [
                d + datetime.timedelta(days=delta)
                for delta in (-2, -1, 1, 2)
                if (d + datetime.timedelta(days=delta)).month == d.month
            ]

            moves: list[tuple[str, datetime.date, Shift]] = []
            for nd in neighbor_days:
                for (slot_d, slot_code), pid in list(self._slot_to_pid.items()):
                    if slot_d == nd:
                        nd_shift = next(
                            s for ad, s in self._pid_to_slots[pid]
                            if ad == nd and s.code == slot_code
                        )
                        moves.append((pid, nd, nd_shift))

            rng.shuffle(moves)
            moves = moves[:n_attempts]   # cap attempts

            for nd_pid, nd, nd_shift in moves:
                if filled:
                    break
                # Temporarily remove the neighbour's existing assignment
                self._unassign(nd_pid, nd, nd_shift)

                # Can they now cover the unfilled slot?
                violations = self._check_constraints(nd_pid, d, shift)
                if violations is None:
                    # Try to fill the vacated neighbour slot with someone else.
                    # Exclude nd_pid from candidates so they cannot self-replace
                    # (they were just unassigned so they'd pass their own checks).
                    new_eligible = [p for p in self._get_eligible(nd, nd_shift)
                                    if p != nd_pid]
                    if new_eligible:
                        replacement = max(
                            new_eligible,
                            key=lambda p: self._score(p, nd, nd_shift),
                        )
                        # Commit the swap
                        self._assign(nd_pid, d, shift)
                        self._assign(replacement, nd, nd_shift)
                        # Update result.assignments
                        result.assignments = [
                            a for a in result.assignments
                            if not (a.physician_id == nd_pid
                                    and a.date == nd
                                    and a.shift.code == nd_shift.code)
                        ]
                        result.assignments.append(Assignment(
                            date=d, shift=shift,
                            physician_id=nd_pid,
                            physician_name=self.submissions[nd_pid].physician_name,
                        ))
                        result.assignments.append(Assignment(
                            date=nd, shift=nd_shift,
                            physician_id=replacement,
                            physician_name=self.submissions[replacement].physician_name,
                        ))
                        filled = True
                    else:
                        # No one can cover the vacated slot — undo
                        self._assign(nd_pid, nd, nd_shift)
                else:
                    # nd_pid can't take the unfilled slot — restore
                    self._assign(nd_pid, nd, nd_shift)

            if not filled:
                still_unfilled.append(unfilled)

        result.unfilled = still_unfilled
        result.stats = self._compute_stats(result)
        return result

    # -------------------------------------------------------------------
    # On-call assignment (runs after regular schedule is complete)
    # -------------------------------------------------------------------

    def assign_on_calls(self, result: ScheduleResult) -> ScheduleResult:
        """
        Assign Day On Call (DOC) and Night On Call (NOC) after the regular
        schedule is fully built.

        Rules:
          - Each physician gets at most 1 on-call per month.
          - On-call cannot be on the same day as a regular shift.
          - Next-day rest: the day after an on-call must be free of regular shifts.
          - DOC preferred during the day; NOC preferred at night — both are
            acceptable; DOC is tried first per available day.
          - Prefer weekdays; avoid weekends where possible.
          - Full-time physicians should ideally each receive 1 call.
          - Unfilled calls are noted but do not block schedule release.
          - Does NOT modify regular assignments — purely additive.
        """
        # Build shift index directly from result — no state restoration needed.
        shift_dates: dict[datetime.date, set[str]] = defaultdict(set)
        for a in result.assignments:
            shift_dates[a.date].add(a.physician_id)

        # Track which (date, call_type) slots are already filled so we never
        # double-book the same call slot (one DOC and one NOC per day max).
        filled_call_slots: set[tuple[datetime.date, str]] = set()
        on_calls: list[OnCallAssignment] = []

        # Build per-physician on-call availability:
        # pid -> [(date, call_type)] sorted weekdays first, then by date
        avail: dict[str, list[tuple[datetime.date, str]]] = {}
        for pid, sub in self.submissions.items():
            # Skip physicians who cannot do call shifts
            cfg_oc = (self.roster.get(pid)
                      or self._roster_lower.get(pid.lower())
                      or self._roster_by_name.get(pid.lower()))
            if cfg_oc and cfg_oc.no_call:
                continue
            days_list: list[tuple[datetime.date, str]] = []
            for day in sub.days:
                if day.doc_available:
                    days_list.append((day.date, "DOC"))
                if day.noc_available:
                    days_list.append((day.date, "NOC"))
            if days_list:
                days_list.sort(key=lambda x: (x[0].weekday() >= 5, x[0]))
                avail[pid] = days_list

        # Greedy assignment: each physician gets at most 1 call.
        for pid, call_days in avail.items():
            sub = self.submissions[pid]
            for call_date, call_type in call_days:
                # Skip if this (date, type) slot already has someone
                if (call_date, call_type) in filled_call_slots:
                    continue
                # Must not have a regular shift on the call day
                if pid in shift_dates.get(call_date, set()):
                    continue
                # Next-day rest: no regular shift the day after the call
                next_day = call_date + datetime.timedelta(days=1)
                if pid in shift_dates.get(next_day, set()):
                    continue
                # Assign
                on_calls.append(OnCallAssignment(
                    date=call_date,
                    call_type=call_type,
                    physician_id=pid,
                    physician_name=sub.physician_name,
                ))
                filled_call_slots.add((call_date, call_type))
                break   # one call per physician per month

        result.on_calls = on_calls
        return result

    # -------------------------------------------------------------------
    # Stats
    # -------------------------------------------------------------------

    def _compute_stats(self, result: ScheduleResult) -> ScheduleStats:
        filled = len(result.assignments)
        total = filled + len(result.unfilled)
        group_a = sum(1 for a in result.assignments if a.shift.site_group == SiteGroup.A)
        group_b = filled - group_a
        counts: dict[str, int] = defaultdict(int)
        # Build per-physician night dates for singleton detection
        night_dates: dict[str, set] = defaultdict(set)
        for a in result.assignments:
            counts[a.physician_id] += 1
            if a.shift.time == "2400h":
                night_dates[a.physician_id].add(a.date)
        # Count singleton 2400h: isolated nights (no adjacent night the day before or after)
        singletons: dict[str, int] = defaultdict(int)
        for pid, nights in night_dates.items():
            for d in nights:
                prev = d - datetime.timedelta(days=1)
                nxt = d + datetime.timedelta(days=1)
                if prev not in nights and nxt not in nights:
                    singletons[pid] += 1
        return ScheduleStats(
            total_slots=total,
            filled_slots=filled,
            unfilled_slots=len(result.unfilled),
            group_a_count=group_a,
            group_b_count=group_b,
            group_a_pct=round(group_a / filled, 3) if filled else 0.0,
            group_b_pct=round(group_b / filled, 3) if filled else 0.0,
            physician_counts=dict(counts),
            physician_singletons=dict(singletons),
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_schedule(
    submissions: list[PhysicianSubmission],
    roster: dict[str, PhysicianConfig],
    config: dict,
    year: int,
    month: int,
) -> ScheduleResult:
    """
    Generate a monthly schedule from physician submissions.

    Parameters
    ----------
    submissions:
        Parsed physician Excel submissions for the month.
    roster:
        Physician configuration from physicians.yaml.
    config:
        Scheduler global config from scheduler_config.yaml.
    year, month:
        Scheduling period.

    Returns
    -------
    ScheduleResult with assignments, unfilled slots, issues, and stats.
    """
    return ScheduleGenerator(submissions, roster, config).generate(year, month)
