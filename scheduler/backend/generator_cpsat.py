"""
CP-SAT based schedule generator for the emergency physician scheduling system.

Uses Google OR-Tools CP-SAT solver to find an optimal assignment of physicians
to shift slots for a given month.  Produces the same ScheduleResult output type
as generator.py so both approaches are interchangeable from server.py's perspective.

Usage:
    from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
    gen = CpsatScheduleGenerator(submissions, roster, config)
    result = gen.generate(year, month, time_limit=90)  # num_workers defaults to 75% of cores

Requirements:
    pip install ortools
"""

from __future__ import annotations

import calendar
import datetime
import logging
import os
import sys
from collections import defaultdict
from typing import Callable, Optional

_FROZEN = getattr(sys, 'frozen', False)

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator import (
    Assignment,
    CandidateOption,
    OnCallAssignment,
    ScheduleResult,
    ScheduleStats,
    UnfilledSlot,
    ViolationReason,
    _DEFAULT_MAX_CONSEC,
    _DEFAULT_MAX_WEEKENDS,
    _HARD_VIOLATION_RULES,
    _WEEKEND_WEEKDAYS,
    _weekend_key,
)
from scheduler.backend.models import PhysicianSubmission
from scheduler.backend.shifts import (
    BLOCKS,
    SHIFT_TO_BLOCK,
    Shift,
    SiteGroup,
    is_next_shift_ok,
    violates_max_spacing,
)

logger = logging.getLogger(__name__)

_ortools_import_error: Exception | None = None
try:
    from ortools.sat.python import cp_model as _cp_model
    _ORTOOLS_AVAILABLE = True
except Exception as _ortools_err:
    _ORTOOLS_AVAILABLE = False
    _ortools_import_error = _ortools_err


# ---------------------------------------------------------------------------
# Solution callback — captures variable values during the solve so we never
# need to call solver.value() after solve() returns (which can deadlock in
# PyInstaller frozen binaries when worker threads have not fully terminated).
# ---------------------------------------------------------------------------

class _SolutionCallback(_cp_model.CpSolverSolutionCallback if _ORTOOLS_AVAILABLE else object):
    """Saves the best solution's shift-variable values on each improving solution."""

    def __init__(self, shift_vars: dict, should_stop: Optional[Callable[[], bool]] = None):
        if _ORTOOLS_AVAILABLE:
            super().__init__()
        self._shift_vars = shift_vars          # (pid, d_idx, shift_code) -> IntVar
        self._should_stop = should_stop        # polled on each improving solution
        self.best_values: dict = {}            # (pid, d_idx, shift_code) -> 0 or 1
        self.best_objective: float = float('-inf')
        self.best_bound: float = 0.0

    def on_solution_callback(self) -> None:
        obj = self.objective_value
        if obj > self.best_objective:
            self.best_objective = obj
            self.best_values = {k: self.value(v) for k, v in self._shift_vars.items()}
            self.best_bound = self.best_objective_bound
        if self._should_stop is not None and self._should_stop():
            self.StopSearch()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_shifts() -> list[Shift]:
    """Return a flat list of every Shift across all blocks."""
    return [shift for block in BLOCKS for shift in block]


def _shift_by_code() -> dict[str, Shift]:
    return {shift.code: shift for block in BLOCKS for shift in block}


# Every distinct site string that appears in BLOCKS — used by
# max_consecutive_same_site (HC-12c) so it never hardcodes the site list
# separately from shifts.py's own definitions.
_ALL_SITES: list[str] = sorted({shift.site for block in BLOCKS for shift in block})


# ---------------------------------------------------------------------------
# CP-SAT solver
# ---------------------------------------------------------------------------

class CpsatScheduleGenerator:
    """
    Constraint-programming (CP-SAT) based schedule generator.

    Produces the same ScheduleResult as ScheduleGenerator but uses OR-Tools
    CP-SAT to search for a provably optimal (or near-optimal) solution within
    the given time limit.

    Parameters
    ----------
    submissions:
        List of PhysicianSubmission objects parsed from the monthly Excel files.
    roster:
        Physician configuration dict keyed by physician_id.
    config:
        Global scheduler config dict (from scheduler_config.yaml).
    """

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

        # Config shortcuts (mirrors ScheduleGenerator.__init__)
        anchor_cfg = config.get("anchor_shifts", {})
        self._anchor_tol: int = anchor_cfg.get("anchor_target_tolerance", 1)
        # Fallback 2400h cap for a physician who stated no 2400h preference
        # at all — see HC-11's use of it for why this exists only for
        # nights and not 0600h.
        self._default_2400h_cap_unstated: int = anchor_cfg.get("default_2400h_cap_unstated", 4)
        self._max_weekends: int = (
            config.get("weekends", {}).get("max_weekends_per_month", _DEFAULT_MAX_WEEKENDS)
        )
        self._max_consec_default: int = (
            config.get("consecutive", {}).get("max_consecutive_shifts", _DEFAULT_MAX_CONSEC)
        )
        self._group_a_target: float = (
            config.get("site_distribution", {}).get("group_a_target", 0.40)
        )

        # Case-insensitive roster lookups
        self._roster_lower: dict = {k.lower(): v for k, v in roster.items()}
        self._roster_by_name: dict = {v.name.lower(): v for v in roster.values()}
        self._pid_lower: dict[str, str] = {pid.lower(): pid for pid in self.submissions}

        # Availability indices
        # (pid, date, block_idx) -> True
        self._avail: set[tuple] = set()
        # (pid, date) -> frozenset[shift_code]  (flat-file specific shifts)
        self._shift_avail: dict[tuple, frozenset] = {}
        for sub in submissions:
            for day in sub.days:
                for b in day.available_blocks:
                    self._avail.add((sub.physician_id, day.date, b))
                if day.requested_shifts:
                    self._shift_avail[(sub.physician_id, day.date)] = day.requested_shifts

        # Mutable assignment state — initialized here so _assign/_unassign work
        # even when called before generate() (e.g. when rebuilding from a loaded
        # schedule via _require_generator).
        self._pid_to_slots: dict[str, list] = defaultdict(list)
        self._slot_to_pid: dict[tuple, str] = {}
        self._shift_count: dict[str, int] = defaultdict(int)
        self._anchor_count: dict[str, int] = defaultdict(int)
        self._weekend_keys: dict[str, set] = defaultdict(set)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def generate(
        self,
        year: int,
        month: int,
        time_limit: float = 60.0,
        num_workers: Optional[int] = None,
        progress_callback: Optional[Callable] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> ScheduleResult:
        """
        Solve the scheduling problem with CP-SAT and return a ScheduleResult.

        If any physician in the roster is flagged `casual`, the single model
        built here adds two extra "lexicographic" solves before the final
        one, to *guarantee* (not just weight toward) the priority order:
        (1) every non-casual physician gets their own requested shift count,
        (2) only then are casual physicians filled up to their own requested
        count, (3) only then does the full objective (which lets non-casual
        physicians go beyond requested, up to their real max) get optimized.
        Casual physicians are hard-capped at their own requested count via
        HC-7 regardless — there's no tier that lets them exceed it.

        This all happens on ONE persistent CpModel with every hard/soft
        constraint built exactly once for every physician (see
        _generate_single_phase): each tier just re-maximizes a different
        objective on that same model and then adds a constraint locking in
        the achieved value before the next tier runs, so later tiers can
        never undo an earlier tier's guarantee. This is necessary — three
        separate models (one per phase) would have no way to know what
        another phase already assigned, so per-physician caps and
        consecutive-shift history wouldn't carry over between them.
        """
        return self._generate_single_phase(
            year, month, time_limit, num_workers, progress_callback, cancel_check
        )

    def _generate_single_phase(
        self,
        year: int,
        month: int,
        time_limit: float = 60.0,
        num_workers: Optional[int] = None,
        progress_callback: Optional[Callable] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> ScheduleResult:
        """
        Solve the scheduling problem with CP-SAT and return a ScheduleResult.

        If OR-Tools is not installed this falls back to the greedy
        ScheduleGenerator so the caller never gets an import error.

        Parameters
        ----------
        year, month:
            The scheduling period.
        time_limit:
            Maximum solver wall-clock time in seconds.  The solver returns
            the best solution found when the limit is reached.
        num_workers:
            Number of parallel search workers for CP-SAT.  CP-SAT releases
            the GIL internally so this genuinely uses multiple cores.
            Defaults to 75% of the machine's CPU cores (min 1) so the UI
            and backend stay responsive during a solve.
        progress_callback:
            Optional callable(current, total, best_score).  CP-SAT does not
            provide per-iteration callbacks so this is called once at the
            midpoint (50%) as a "Solving…" indicator and once on completion.
        cancel_check:
            Optional callable() -> bool, polled on every improving solution.
            When it returns True, the solver is stopped early via
            StopSearch() and the best solution found so far is returned —
            same code path as hitting the time limit, just sooner.
        """
        if num_workers is None:
            num_workers = max(1, round((os.cpu_count() or 4) * 0.75))

        if not _ORTOOLS_AVAILABLE:
            logger.warning(
                "ortools not available (%s: %s) — falling back to greedy ScheduleGenerator.",
                type(_ortools_import_error).__name__, _ortools_import_error,
            )
            from scheduler.backend.generator import ScheduleGenerator
            gen = ScheduleGenerator(list(self.submissions.values()), self.roster, self.config)
            return gen.run_best_of(
                400, year, month,
                progress_callback=progress_callback,
            )

        if progress_callback:
            progress_callback(0, 100, 0.0)

        days_in_month = calendar.monthrange(year, month)[1]
        all_dates = [datetime.date(year, month, d) for d in range(1, days_in_month + 1)]
        all_shifts_flat = _all_shifts()
        shift_by_code = _shift_by_code()
        pids = list(self.submissions.keys())

        # Signal "solving" at 50% before blocking on the solver
        if progress_callback:
            progress_callback(50, 100, 0.0)

        model = _cp_model.CpModel()

        # ----------------------------------------------------------------
        # Decision variables
        # shifts[(pid, d_idx, shift_code)] = BoolVar
        # ----------------------------------------------------------------
        shifts: dict[tuple, object] = {}
        for pid in pids:
            for d_idx, d in enumerate(all_dates):
                for block in BLOCKS:
                    for shift in block:
                        shifts[(pid, d_idx, shift.code)] = model.new_bool_var(
                            f"s_{pid}_{d_idx}_{shift.code}"
                        )

        # ----------------------------------------------------------------
        # Hard constraint helpers
        # ----------------------------------------------------------------

        def _get_cfg(pid: str) -> Optional[PhysicianConfig]:
            return (
                self.roster.get(pid)
                or self._roster_lower.get(pid.lower())
                or self._roster_by_name.get(pid.lower())
            )

        def _forbidden_sites(pid: str) -> list[str]:
            cfg = _get_cfg(pid)
            return cfg.forbidden_sites if cfg else []

        def _max_consec(pid: str) -> int:
            cfg = _get_cfg(pid)
            return cfg.max_consecutive_shifts if cfg else self._max_consec_default

        def _eff_max_weekends(pid: str) -> int:
            cfg = _get_cfg(pid)
            if cfg and cfg.max_weekends is not None:
                return cfg.max_weekends
            return self._max_weekends

        # ----------------------------------------------------------------
        # HC-1: At most one physician per slot per day
        # ----------------------------------------------------------------
        for d_idx in range(len(all_dates)):
            for block in BLOCKS:
                for shift in block:
                    model.add(
                        sum(shifts[(pid, d_idx, shift.code)] for pid in pids) <= 1
                    )

        # ----------------------------------------------------------------
        # HC-2: Physician works at most one shift per day
        # ----------------------------------------------------------------
        for pid in pids:
            for d_idx in range(len(all_dates)):
                model.add(
                    sum(
                        shifts[(pid, d_idx, shift.code)]
                        for block in BLOCKS
                        for shift in block
                    ) <= 1
                )

        # ----------------------------------------------------------------
        # HC-3: Availability — block-level
        # HC-4: Forbidden sites
        # HC-5: only_2400h / only_0600h restriction
        # HC-6: Flat-file specific shift availability
        # ----------------------------------------------------------------
        for pid in pids:
            cfg = _get_cfg(pid)
            forbidden = _forbidden_sites(pid)
            only_nights = cfg.only_2400h if cfg else False
            only_mornings = cfg.only_0600h if cfg else False

            for d_idx, d in enumerate(all_dates):
                day_specific_shifts = self._shift_avail.get((pid, d))

                for block_idx, block in enumerate(BLOCKS):
                    avail_for_block = (pid, d, block_idx) in self._avail

                    for shift in block:
                        var = shifts[(pid, d_idx, shift.code)]

                        # Unavailable block — unless a flat-file specific shift overrides it.
                        # Flat-file imports set _shift_avail entries per-shift rather than
                        # per-block, so a physician may be available for a single shift even
                        # if the full block is not in _avail.
                        if not avail_for_block:
                            if day_specific_shifts is None or shift.code not in day_specific_shifts:
                                model.add(var == 0)
                                continue
                            # else: specific shift is explicitly available — fall through

                        # Flat-file specific shift restriction (when block IS available)
                        elif day_specific_shifts is not None and shift.code not in day_specific_shifts:
                            model.add(var == 0)
                            continue

                        # Forbidden site
                        if shift.site in forbidden:
                            model.add(var == 0)
                            continue

                        # only_2400h / only_0600h restriction
                        if only_nights and shift.time != "2400h":
                            model.add(var == 0)
                            continue
                        if only_mornings and shift.time != "0600h":
                            model.add(var == 0)
                            continue

                        # Forbidden shift times (e.g. no 0600h or 2400h)
                        forbidden_times = set(cfg.forbidden_shift_times) if cfg else set()
                        if shift.time in forbidden_times:
                            model.add(var == 0)
                            continue

        # ----------------------------------------------------------------
        # HC-7: Max shifts hard cap (cap_at_requested / casual override shifts_max)
        # Casual physicians are always hard-capped at their own requested
        # count — there is no tier that lets them go beyond it, unlike
        # non-casual physicians who may be filled up to shifts_max once
        # every non-casual physician has their requested count (see the
        # lexicographic tiers below).
        # ----------------------------------------------------------------
        for pid in pids:
            sub = self.submissions[pid]
            cfg = _get_cfg(pid)
            is_casual = bool(cfg and cfg.casual)
            if is_casual or (cfg and cfg.cap_at_requested and sub.shifts_requested > 0):
                hard_max = sub.shifts_requested
            else:
                hard_max = sub.shifts_max if sub.shifts_max > 0 else sub.shifts_requested
            if hard_max > 0:
                model.add(
                    sum(
                        shifts[(pid, d_idx, shift.code)]
                        for d_idx in range(len(all_dates))
                        for block in BLOCKS
                        for shift in block
                    ) <= hard_max
                )

        # ----------------------------------------------------------------
        # HC-8: Consecutive day limit
        # For any window of (max_consec+1) consecutive days, physician
        # works at most max_consec of them.
        # ----------------------------------------------------------------
        for pid in pids:
            mc = _max_consec(pid)
            window_size = mc + 1
            if window_size <= len(all_dates):
                for start in range(len(all_dates) - mc):
                    window_vars = []
                    for d_idx in range(start, start + window_size):
                        for block in BLOCKS:
                            for shift in block:
                                window_vars.append(shifts[(pid, d_idx, shift.code)])
                    model.add(sum(window_vars) <= mc)

        # ----------------------------------------------------------------
        # HC-9: 23h spacing between adjacent-day shifts
        # For each pair of shifts on consecutive days (or 2-days-apart with
        # 2400h), add: var1 + var2 <= 1 if the gap would be < 23h (hard —
        # never allowed). A gap > 36h (e.g. 0600h then 2400h the next day)
        # is handled separately below as a SOFT rule: penalized heavily in
        # the objective rather than forbidden outright, so the solver can
        # still accept it under pressure rather than leave a slot unfilled.
        # ----------------------------------------------------------------
        long_gap_penalty_terms: list = []
        # Heavier than every other soft-preference weight in this file
        # (which top out around 30-40) so the 36h rule holds in the large
        # majority of a solved schedule, but still well under the 1000
        # per-filled-shift weight so it yields rather than leave gaps unfilled.
        _LONG_GAP_PENALTY = 300

        # Soft: discourage a big swing in start time between two consecutive
        # working days (e.g. 0600h one day, 1800h the next) even though the
        # rest gap is technically fine — a day organized around an early
        # shift one day and a late shift the next is disruptive in a way
        # the pure rest-hours check doesn't capture. Cascading later across
        # several days (0600 → 0900 → 1200 → ...) never trips this, since
        # each individual day-to-day step stays under the threshold; only a
        # single big jump does. Same weight class as run_penalty_terms (35)
        # below — a real preference, not close to overriding fill/rest.
        swing_penalty_terms: list = []
        _SHIFT_SWING_MAX_HOURS = 10
        _SWING_PENALTY = 35

        for pid in pids:
            for d_idx in range(len(all_dates) - 1):
                d1 = all_dates[d_idx]
                d2 = all_dates[d_idx + 1]
                # consecutive days: gap == 1
                for block1 in BLOCKS:
                    for shift1 in block1:
                        for block2 in BLOCKS:
                            for shift2 in block2:
                                if not is_next_shift_ok(shift1, 1, shift2):
                                    model.add(
                                        shifts[(pid, d_idx, shift1.code)]
                                        + shifts[(pid, d_idx + 1, shift2.code)] <= 1
                                    )
                                    continue
                                if violates_max_spacing(shift1, shift2):
                                    long_gap = model.new_bool_var(
                                        f"longgap_{pid}_{d_idx}_{shift1.code}_{shift2.code}"
                                    )
                                    model.add(
                                        shifts[(pid, d_idx, shift1.code)]
                                        + shifts[(pid, d_idx + 1, shift2.code)] <= 1 + long_gap
                                    )
                                    long_gap_penalty_terms.append(-_LONG_GAP_PENALTY * long_gap)
                                if abs(shift1.start_hour - shift2.start_hour) > _SHIFT_SWING_MAX_HOURS:
                                    swing = model.new_bool_var(
                                        f"swing_{pid}_{d_idx}_{shift1.code}_{shift2.code}"
                                    )
                                    model.add(
                                        shifts[(pid, d_idx, shift1.code)]
                                        + shifts[(pid, d_idx + 1, shift2.code)] <= 1 + swing
                                    )
                                    swing_penalty_terms.append(-_SWING_PENALTY * swing)

            # 2-day gap: only 2400h → next morning matters (36h rest rule)
            for d_idx in range(len(all_dates) - 2):
                for block1 in BLOCKS:
                    for shift1 in block1:
                        if shift1.time != "2400h":
                            continue
                        for block2 in BLOCKS:
                            for shift2 in block2:
                                if not is_next_shift_ok(shift1, 2, shift2):
                                    model.add(
                                        shifts[(pid, d_idx, shift1.code)]
                                        + shifts[(pid, d_idx + 2, shift2.code)] <= 1
                                    )

        # ----------------------------------------------------------------
        # HC-9a: Linked-rest pairs (scheduler_config.yaml's
        # linked_rest_pairs, e.g. Kenneth Lam / Michelle Rico). Two
        # physicians who are separate roster identities for shift-count
        # and priority purposes but must be treated as a single combined
        # timeline for rest spacing, shift-time-swing, and consecutive-run
        # purposes: never both scheduled the same day, the exact same
        # 23h-minimum / 36h-maximum / >10h-swing rules that apply to one
        # physician's own day-to-day sequence apply across their combined
        # sequence — checked in both directions (A→B and B→A) since this
        # is a symmetric pair relationship, not one person's own
        # forward-only timeline — and their COMBINED consecutive-day run
        # is capped the same way HC-8 caps one physician's own run.
        # Without that last part, HC-8 only bounds each member's own run
        # separately, so one finishing a run right as the other starts
        # could chain into a combined run far longer than either's
        # individual cap — confirmed against the human schedule: their
        # real pre-split combined runs topped out at 5 days, matching
        # each member's own max_consecutive_shifts, never 10.
        # ----------------------------------------------------------------
        for rule in self.config.get("linked_rest_pairs", []):
            phys = [p for p in rule.get("physicians", []) if p in pids]
            if len(phys) != 2:
                continue
            pid_a, pid_b = phys

            # Never both scheduled the same day, unconditionally.
            for d_idx in range(len(all_dates)):
                works_a = sum(shifts[(pid_a, d_idx, s.code)] for block in BLOCKS for s in block)
                works_b = sum(shifts[(pid_b, d_idx, s.code)] for block in BLOCKS for s in block)
                model.add(works_a + works_b <= 1)

            # Combined consecutive-day limit (see docstring above).
            mc = min(_max_consec(pid_a), _max_consec(pid_b))
            window_size = mc + 1
            if window_size <= len(all_dates):
                for start in range(len(all_dates) - mc):
                    window_vars = []
                    for d_idx in range(start, start + window_size):
                        for block in BLOCKS:
                            for shift in block:
                                window_vars.append(shifts[(pid_a, d_idx, shift.code)])
                                window_vars.append(shifts[(pid_b, d_idx, shift.code)])
                    model.add(sum(window_vars) <= mc)

            for first, second in ((pid_a, pid_b), (pid_b, pid_a)):
                for d_idx in range(len(all_dates) - 1):
                    for block1 in BLOCKS:
                        for shift1 in block1:
                            for block2 in BLOCKS:
                                for shift2 in block2:
                                    if not is_next_shift_ok(shift1, 1, shift2):
                                        model.add(
                                            shifts[(first, d_idx, shift1.code)]
                                            + shifts[(second, d_idx + 1, shift2.code)] <= 1
                                        )
                                        continue
                                    if violates_max_spacing(shift1, shift2):
                                        long_gap = model.new_bool_var(
                                            f"longgap_link_{first}_{second}_{d_idx}_{shift1.code}_{shift2.code}"
                                        )
                                        model.add(
                                            shifts[(first, d_idx, shift1.code)]
                                            + shifts[(second, d_idx + 1, shift2.code)] <= 1 + long_gap
                                        )
                                        long_gap_penalty_terms.append(-_LONG_GAP_PENALTY * long_gap)
                                    if abs(shift1.start_hour - shift2.start_hour) > _SHIFT_SWING_MAX_HOURS:
                                        swing = model.new_bool_var(
                                            f"swing_link_{first}_{second}_{d_idx}_{shift1.code}_{shift2.code}"
                                        )
                                        model.add(
                                            shifts[(first, d_idx, shift1.code)]
                                            + shifts[(second, d_idx + 1, shift2.code)] <= 1 + swing
                                        )
                                        swing_penalty_terms.append(-_SWING_PENALTY * swing)

                # 2-day gap: only 2400h → next morning matters (36h rest rule)
                for d_idx in range(len(all_dates) - 2):
                    for block1 in BLOCKS:
                        for shift1 in block1:
                            if shift1.time != "2400h":
                                continue
                            for block2 in BLOCKS:
                                for shift2 in block2:
                                    if not is_next_shift_ok(shift1, 2, shift2):
                                        model.add(
                                            shifts[(first, d_idx, shift1.code)]
                                            + shifts[(second, d_idx + 2, shift2.code)] <= 1
                                        )

        # ----------------------------------------------------------------
        # HC-9b: Conditional co-working (scheduler_config.yaml's
        # conditional_cowork, e.g. Edgecumbe/Houston). Ported from the
        # greedy generator's soft check (generator.py _check_constraints,
        # rule "cowork_condition") into hard CP-SAT constraints — previously
        # this config existed but was never enforced by the CP-SAT solver.
        #
        #   no_shared_weekends: the two physicians can never both have a
        #       shift on the same weekend date.
        #   weekday_requires_one_at: on a weekday where both work, EXACTLY
        #       one of them must be on the given shift time. Modelled with
        #       pure linear constraints (no reification needed):
        #         at_A + at_B <= 1                  — not both at that time
        #         works_A + works_B <= 1 + at_A + at_B  — not neither, if both work
        # ----------------------------------------------------------------
        for rule in self.config.get("conditional_cowork", []):
            phys = [p for p in rule.get("physicians", []) if p in pids]
            if len(phys) != 2:
                continue
            pid_a, pid_b = phys
            required_time = rule.get("weekday_requires_one_at")
            for d_idx, d in enumerate(all_dates):
                works_a = sum(shifts[(pid_a, d_idx, s.code)] for block in BLOCKS for s in block)
                works_b = sum(shifts[(pid_b, d_idx, s.code)] for block in BLOCKS for s in block)
                if d.weekday() in _WEEKEND_WEEKDAYS:
                    if rule.get("no_shared_weekends"):
                        model.add(works_a + works_b <= 1)
                elif required_time:
                    at_a = sum(shifts[(pid_a, d_idx, s.code)] for block in BLOCKS for s in block if s.time == required_time)
                    at_b = sum(shifts[(pid_b, d_idx, s.code)] for block in BLOCKS for s in block if s.time == required_time)
                    model.add(at_a + at_b <= 1)
                    model.add(works_a + works_b <= 1 + at_a + at_b)

        # ----------------------------------------------------------------
        # HC-9c: Timed separation (scheduler_config.yaml's timed_separation,
        # e.g. Brenneis/Fanaeian). If both physicians work the same day,
        # their shift start times must be at least min_hours_gap apart, and
        # any (time1, time2) combination listed in forbidden_time_pairs is
        # banned outright regardless of gap. Hard rule, ported from the
        # greedy generator's "10. Timed separation" check — previously
        # defined in config but never enforced by CP-SAT.
        # ----------------------------------------------------------------
        all_shifts_list = [s for block in BLOCKS for s in block]
        for rule in self.config.get("timed_separation", []):
            phys = [p for p in rule.get("physicians", []) if p in pids]
            if len(phys) != 2:
                continue
            pid_a, pid_b = phys
            min_gap = rule.get("min_hours_gap", 0)
            forbidden_pairs = {frozenset(p) for p in rule.get("forbidden_time_pairs", [])}
            for d_idx in range(len(all_dates)):
                for shift_a in all_shifts_list:
                    for shift_b in all_shifts_list:
                        gap = abs(shift_a.start_hour - shift_b.start_hour)
                        is_forbidden_pair = frozenset((shift_a.time, shift_b.time)) in forbidden_pairs
                        if gap < min_gap or is_forbidden_pair:
                            model.add(
                                shifts[(pid_a, d_idx, shift_a.code)]
                                + shifts[(pid_b, d_idx, shift_b.code)] <= 1
                            )

        # ----------------------------------------------------------------
        # HC-10: Same-shift-code on adjacent days forbidden
        # ----------------------------------------------------------------
        for pid in pids:
            for d_idx in range(len(all_dates) - 1):
                for block in BLOCKS:
                    for shift in block:
                        model.add(
                            shifts[(pid, d_idx, shift.code)]
                            + shifts[(pid, d_idx + 1, shift.code)] <= 1
                        )

        # ----------------------------------------------------------------
        # HC-11: Anchor shift limit (0600h + 2400h)
        # ----------------------------------------------------------------
        # The global anchor_max is a FALLBACK for physicians who have not
        # explicitly requested specific numbers of anchor shifts.  For
        # physicians like LamRico who request 16 2400h shifts, the global
        # cap of 4 must NOT override their explicit per-physician request —
        # doing so is the bug that caused them to receive only 4-6 shifts.
        #
        # anchor_overage_penalty_terms: the hard cap above allows a
        # physician up to their own stated 2400h/0600h request PLUS
        # anchor_target_tolerance — a genuine safety valve for when a slot
        # would otherwise go unfilled, mirroring how a human scheduler
        # occasionally has to ask someone for "just one more" night. But
        # with no cost attached, the solver was reaching for that valve
        # constantly rather than rarely: a real October run gave ~38-40%
        # of anchor-requesting physicians one more 2400h/0600h than they
        # asked for, vs. under 7% in the human-built schedule for the same
        # month. This penalty makes using the tolerance actually cost
        # something, so it only gets spent when a slot genuinely can't be
        # filled any other way — the fill reward (~1000+/shift) still
        # outweighs this penalty when that's truly the only option.
        #
        # 250 wasn't enough on its own: a follow-up real run still pushed
        # 2400h overage on 8 physicians, and checking every one of those
        # nights via the candidate-eligibility logic found a fully clean,
        # unused alternative candidate in every single case (frequently a
        # physician not even at their own cap yet) — i.e. every instance
        # was avoidable, not need-driven. 250 could still lose out to a
        # combination of other secondary preferences (group balance,
        # clustering, weekend spread — each in the 20-35 range, or the
        # 300-weighted long-gap penalty). Raised to 500, comfortably above
        # every other secondary term, so this only yields when truly
        # nothing else can fill the slot.
        anchor_overage_penalty_terms = []
        # Positive counterpart to the overage penalty below: reward filling
        # a physician's own explicit 0600h/2400h request with the matching
        # shift type, not just counting toward their total requested shift
        # count. Without this, nothing prefers giving a physician who
        # explicitly asked for (say) 6 0600h shifts an actual 0600h slot
        # over some other time — only their total shift count was rewarded,
        # and the cap below only stops them going OVER their request, it
        # never pulls them toward it. Anchor shifts are generally less
        # desirable, so honouring an explicit request for them first means
        # fewer of them have to fall on physicians who never asked for one.
        # Weighted just under the total-shift-count bonus_high (100, below)
        # so reaching a physician's overall requested count always still
        # wins first if the two ever genuinely conflict, but this clearly
        # outranks the everyday secondary terms (group balance, clustering,
        # site preference — each 20-35).
        anchor_fulfillment_bonus_terms = []
        _ANCHOR_FULFILLMENT_BONUS = 90
        _ANCHOR_OVERAGE_PENALTY = 500
        # A physician's stated numbers can themselves signal which anchor
        # type they'd rather absorb overage in — e.g. explicitly wanting 0
        # 0600h and a real positive 2400h count says "give me another
        # night before ever putting me on an early start". When overage is
        # genuinely unavoidable, it should preferentially land on whoever
        # already leans that way, not be indifferent between anyone. A
        # roster-level anchor_preference (physicians.yaml) always wins when
        # set; otherwise inferred fresh from this month's own submission.
        _ANCHOR_OVERAGE_PENALTY_PREFERRED = 150

        def _anchor_preference(pid: str, sub) -> Optional[str]:
            cfg = _get_cfg(pid)
            if cfg and cfg.anchor_preference in ("2400h", "0600h"):
                return cfg.anchor_preference
            if (sub.shifts_2400h_stated and sub.shifts_2400h_requested > 0
                    and sub.shifts_0600h_stated and sub.shifts_0600h_requested == 0):
                return "2400h"
            if (sub.shifts_0600h_stated and sub.shifts_0600h_requested > 0
                    and sub.shifts_2400h_stated and sub.shifts_2400h_requested == 0):
                return "0600h"
            return None

        # anchor_fill_vars_flat: every fill_2400/fill_0600 IntVar, unweighted —
        # the objective for the anchor-floor lexicographic tier below.
        # anchor_requests_by_pid: pid -> [("2400h", requested), ...] for
        # whichever type(s) the physician actually stated a positive request
        # for. The solution callback only tracks `shifts` vars (not these
        # derived IntVars), so the tier's lock-in step recomputes each
        # physician's achieved count straight from the shift assignments —
        # this list is what tells it which (type, requested-cap) pairs to
        # recompute and lock, per physician rather than just the roster-wide
        # sum (see that tier's own comment for why the per-physician part
        # matters).
        anchor_fill_vars_flat: list = []
        anchor_requests_by_pid: dict[str, list[tuple[str, int]]] = {}

        for pid in pids:
            sub = self.submissions[pid]
            anchor_vars = []
            for d_idx in range(len(all_dates)):
                for block in BLOCKS:
                    for shift in block:
                        if shift.time in ("0600h", "2400h"):
                            anchor_vars.append(shifts[(pid, d_idx, shift.code)])

            if anchor_vars:
                vars_2400 = [
                    shifts[(pid, d_idx, shift.code)]
                    for d_idx in range(len(all_dates))
                    for block in BLOCKS
                    for shift in block
                    if shift.time == "2400h"
                ]
                vars_0600 = [
                    shifts[(pid, d_idx, shift.code)]
                    for d_idx in range(len(all_dates))
                    for block in BLOCKS
                    for shift in block
                    if shift.time == "0600h"
                ]

                if sub.shifts_2400h_stated and sub.shifts_2400h_requested > 0 and vars_2400:
                    fill_2400 = model.new_int_var(0, sub.shifts_2400h_requested, f"anchorfill_2400_{pid}")
                    model.add(fill_2400 <= sum(vars_2400))
                    anchor_fulfillment_bonus_terms.append(_ANCHOR_FULFILLMENT_BONUS * fill_2400)
                    anchor_fill_vars_flat.append(fill_2400)
                    anchor_requests_by_pid.setdefault(pid, []).append(("2400h", sub.shifts_2400h_requested))
                if sub.shifts_0600h_stated and sub.shifts_0600h_requested > 0 and vars_0600:
                    fill_0600 = model.new_int_var(0, sub.shifts_0600h_requested, f"anchorfill_0600_{pid}")
                    model.add(fill_0600 <= sum(vars_0600))
                    anchor_fulfillment_bonus_terms.append(_ANCHOR_FULFILLMENT_BONUS * fill_0600)
                    anchor_fill_vars_flat.append(fill_0600)
                    anchor_requests_by_pid.setdefault(pid, []).append(("0600h", sub.shifts_0600h_requested))

                # Per-physician 2400h cap. Checked via shifts_2400h_stated,
                # not `> 0` — a physician who explicitly typed "0" (a real
                # "I want none") and one who left the cell blank (no signal
                # at all) both parse to the same int 0, but must be treated
                # oppositely: an explicit 0 gets the tight requested+tolerance
                # cap below (effectively "almost never"), not the more
                # permissive unstated fallback.
                if sub.shifts_2400h_stated:
                    pid_cap_2400 = sub.shifts_2400h_requested + self._anchor_tol
                    if vars_2400:
                        model.add(sum(vars_2400) <= pid_cap_2400)
                        if self._anchor_tol > 0:
                            over_2400 = model.new_int_var(0, self._anchor_tol, f"over2400_{pid}")
                            model.add(over_2400 >= sum(vars_2400) - sub.shifts_2400h_requested)
                            weight_2400 = (
                                _ANCHOR_OVERAGE_PENALTY_PREFERRED
                                if _anchor_preference(pid, sub) == "2400h"
                                else _ANCHOR_OVERAGE_PENALTY
                            )
                            anchor_overage_penalty_terms.append(-weight_2400 * over_2400)
                elif vars_2400:
                    # No stated 2400h preference at all. Unlike 0600h below,
                    # this still gets a firm fallback ceiling: night-shift
                    # burden is a fairness/wellbeing concern in a way early
                    # starts aren't, so leaving it fully unbounded risks
                    # quietly loading someone up with midnight shifts just
                    # because they left the field blank, not because they
                    # want or can handle it. Kept deliberately conservative
                    # (flat, not tied to their total shift count) until
                    # there's a reliable way to know who actually prefers
                    # nights.
                    model.add(sum(vars_2400) <= self._default_2400h_cap_unstated)

                # Per-physician 0600h cap — only when shifts_0600h_stated
                # (same explicit-0-vs-blank distinction as 2400h above).
                # No fallback ceiling for a genuinely blank cell: a guessed
                # default here was tried (flat, then proportional to total
                # shifts) and confirmed wrong against real data — real
                # physicians who truly state no anchor preference take on
                # far more than any guess would allow. 0600h carries none
                # of 2400h's fairness/wellbeing concern, so there's no
                # reason to guess a limit here the way there is for nights
                # — but an explicit 0 is real signal, not a blank, and
                # still gets capped below like any other explicit request.
                if sub.shifts_0600h_stated:
                    pid_cap_0600 = sub.shifts_0600h_requested + self._anchor_tol
                    if vars_0600:
                        model.add(sum(vars_0600) <= pid_cap_0600)
                        if self._anchor_tol > 0:
                            over_0600 = model.new_int_var(0, self._anchor_tol, f"over0600_{pid}")
                            model.add(over_0600 >= sum(vars_0600) - sub.shifts_0600h_requested)
                            weight_0600 = (
                                _ANCHOR_OVERAGE_PENALTY_PREFERRED
                                if _anchor_preference(pid, sub) == "0600h"
                                else _ANCHOR_OVERAGE_PENALTY
                            )
                            anchor_overage_penalty_terms.append(-weight_0600 * over_0600)

        # ----------------------------------------------------------------
        # HC-12: Weekend limit
        # For each physician, for each distinct weekend cluster (Fri/Sat/Sun),
        # create a BoolVar that is 1 iff the physician works any shift in that
        # cluster.  Then cap the total weekend BoolVars.
        # ----------------------------------------------------------------
        # Group date indices by weekend key
        weekend_clusters: dict[tuple, list[int]] = defaultdict(list)
        for d_idx, d in enumerate(all_dates):
            if d.weekday() in _WEEKEND_WEEKDAYS:
                weekend_clusters[_weekend_key(d)].append(d_idx)

        # Captured per-pid so the weekend-clumping soft term (below) can
        # reuse these same "worked this weekend" BoolVars instead of
        # rebuilding them.
        weekend_worked_vars_by_pid: dict[str, list] = {}
        for pid in pids:
            max_we = _eff_max_weekends(pid)
            if not weekend_clusters:
                continue

            weekend_worked_vars = []
            for wk_key, d_indices in weekend_clusters.items():
                # BoolVar = 1 iff physician works any shift in this cluster
                wv = model.new_bool_var(f"wknd_{pid}_{wk_key[2]}")
                # Sum of all shift vars in this cluster
                cluster_shifts = [
                    shifts[(pid, d_idx, shift.code)]
                    for d_idx in d_indices
                    for block in BLOCKS
                    for shift in block
                ]
                if cluster_shifts:
                    # wv == 1 iff any cluster shift == 1
                    total_cluster = sum(cluster_shifts)
                    # wv >= each individual var  →  wv == 1 if any is 1
                    for cv in cluster_shifts:
                        model.add(wv >= cv)
                    # wv <= total  →  wv can only be 1 if at least one is 1
                    model.add(wv <= total_cluster)
                    weekend_worked_vars.append(wv)

            if weekend_worked_vars:
                model.add(sum(weekend_worked_vars) <= max_we)
                weekend_worked_vars_by_pid[pid] = weekend_worked_vars

        # Soft: weekend-clumping penalty (prefer_weekend_clumping). Penalizes
        # each distinct weekend touched, so — for a similar total number of
        # weekend shifts — the solver prefers concentrating them onto fewer
        # weekends (e.g. one Fri/Sat/Sun stretch) over spreading them thin.
        # Distinct from max_weekends above, which only caps the count.
        _WEEKEND_CLUMP_PENALTY = 15
        weekend_clump_penalty_terms = []
        for pid in pids:
            cfg = _get_cfg(pid)
            if not (cfg and cfg.prefer_weekend_clumping):
                continue
            for wv in weekend_worked_vars_by_pid.get(pid, []):
                weekend_clump_penalty_terms.append(-_WEEKEND_CLUMP_PENALTY * wv)

        # ----------------------------------------------------------------
        # HC-13: Max consecutive nights (NIAR)
        # ----------------------------------------------------------------
        for pid in pids:
            cfg = _get_cfg(pid)
            max_nights = cfg.max_consecutive_nights if cfg else self._max_consec_default
            window_size = max_nights + 1
            if window_size <= len(all_dates):
                night_vars_by_day = []
                for d_idx in range(len(all_dates)):
                    day_night_vars = [
                        shifts[(pid, d_idx, shift.code)]
                        for block in BLOCKS
                        for shift in block
                        if shift.time == "2400h"
                    ]
                    night_vars_by_day.append(day_night_vars)

                for start in range(len(all_dates) - max_nights):
                    window_night_vars = []
                    for d_idx in range(start, start + window_size):
                        window_night_vars.extend(night_vars_by_day[d_idx])
                    model.add(sum(window_night_vars) <= max_nights)

        # ----------------------------------------------------------------
        # Objective function
        # Maximize: filled slots (primary) + soft bonuses
        #
        # Soft terms (all scaled so filled slots dominate):
        #   +50  per slot filled that the physician specifically requested (date bonus)
        #   +50  per shift up to physician's requested count (capped IntVar)
        #   +3   per shift assigned (small linear incentive to fill toward max)
        #   +30  per Group A shift up to A-floor target (raises A:B balance incentive)
        #   +6   per preferred Group B site shift (group_b_site_preference)
        #   +20  per A→B or B→A consecutive pair (alternation reward)
        #   -40  per A→A consecutive pair (penalty)
        #   +18  per consecutive 2400h night pair (singleton clustering)
        #   +10  per consecutive working-day pair, any shift type (reduces isolated days)
        #   -30  per consecutive 2400h pair for prefer_singleton_nights physicians
        #   -35  per 4-consecutive-day run (for physicians with mc >= 4)
        # ----------------------------------------------------------------

        # Total filled slots — weighted per shift category so the hardest
        # slots to staff are prioritized when the solver can't fill
        # everything. Base 1000 (same as before) plus additive bonuses:
        #   +300 2400h (midnight) shifts — historically the hardest to fill
        #   +200 weekend shifts (any time) — second hardest
        #   +100 0600h shifts — third
        # Additive so a Saturday 2400h shift (1500) outranks a plain
        # weekday 0600h shift (1100), which outranks an ordinary weekday
        # daytime shift (1000). This only affects which slots the solver
        # leaves unfilled under pressure — it does not change how the
        # final fill-rate stat is computed (that's a separate post-solve
        # count, unaffected by objective weighting).
        _FILL_WEIGHT_2400H = 300
        _FILL_WEIGHT_WEEKEND = 200
        _FILL_WEIGHT_0600H = 100

        weighted_fill_terms = []
        for pid in pids:
            for d_idx, d in enumerate(all_dates):
                is_weekend = d.weekday() in _WEEKEND_WEEKDAYS
                for block in BLOCKS:
                    for shift in block:
                        weight = 1000
                        if shift.time == "2400h":
                            weight += _FILL_WEIGHT_2400H
                        if is_weekend:
                            weight += _FILL_WEIGHT_WEEKEND
                        if shift.time == "0600h":
                            weight += _FILL_WEIGHT_0600H
                        weighted_fill_terms.append(weight * shifts[(pid, d_idx, shift.code)])
        filled_expr = sum(weighted_fill_terms)

        # Soft: requests bonus
        request_bonus_terms = []
        for pid in pids:
            sub = self.submissions[pid]
            cfg = _get_cfg(pid)
            honor = cfg.honor_all_requests if cfg else False
            for d_idx, d in enumerate(all_dates):
                day_shifts_req = self._shift_avail.get((pid, d))
                if day_shifts_req:
                    for shift_code in day_shifts_req:
                        if (pid, d_idx, shift_code) in shifts:
                            weight = 150 if honor else 5
                            request_bonus_terms.append(
                                weight * shifts[(pid, d_idx, shift_code)]
                            )

        # Soft: per-physician requested-count bonus
        # Use a capped IntVar so the marginal reward for the kth shift is:
        #   k <= requested : +1000 (fill) + 50 (cap bonus) + 3 (linear) = 1053
        #   k >  requested : +1000 (fill) + 3 (linear)                  = 1003
        # This strongly prefers completing underscheduled physicians before
        # overscheduling physicians already at their requested count.
        physician_shift_exprs: dict[str, object] = {}
        for pid in pids:
            physician_shift_exprs[pid] = sum(
                shifts[(pid, d_idx, shift.code)]
                for d_idx in range(len(all_dates))
                for block in BLOCKS
                for shift in block
            )

        # Per-physician per-day "worked any shift" BoolVar — used for run-length penalties.
        # HC-2 guarantees sum of shift vars per (pid, day) is 0 or 1, so equality is safe.
        worked_bool: dict[tuple, object] = {}
        for pid in pids:
            for d_idx in range(len(all_dates)):
                day_vars = [
                    shifts[(pid, d_idx, shift.code)]
                    for block in BLOCKS
                    for shift in block
                ]
                w = model.new_bool_var(f"w_{pid}_{d_idx}")
                model.add(sum(day_vars) == w)
                worked_bool[(pid, d_idx)] = w

        # requested_bonus_by_pid captures each physician's own bonus expression
        # so the lexicographic tiers below (normal-vs-casual priority) can sum
        # a subset of them into a tier-specific objective. Every physician
        # not flagged casual is "normal" for this purpose.
        #
        # Split into two tiers (first half of requested vs. second half)
        # instead of one flat-weight bonus, so the marginal value of a
        # physician's shifts decreases as they approach their own requested
        # count. A flat per-shift bonus makes the objective indifferent
        # between giving a contested slot to someone already close to their
        # cap vs. someone still far below it — both are worth the same +1
        # marginal unit — so scarce slots can end up spread thin across many
        # people instead of actually closing anyone's gap (confirmed via a
        # real run: Lam-Rico and MacGougan, both with real availability and
        # a real target, landed far under it while other physicians got
        # topped up instead). Weighting the first half higher makes closing
        # a large existing gap worth more than incrementally topping off
        # someone already near their own target.
        #
        # priority_weight (physicians.yaml) scales both tiers for a
        # physician whose requested count should carry more weight than the
        # general population (e.g. a department chief) — never applied to a
        # casual physician, who stays in their own separate, strictly-lower
        # priority tier regardless of this value.
        requested_bonus_by_pid: dict[str, object] = {}
        effective_requested_by_pid: dict[str, int] = {}
        deficit_penalty_terms = []
        for pid in pids:
            sub = self.submissions[pid]
            cfg = _get_cfg(pid)
            effective_requested = sub.shifts_requested
            if effective_requested == 0 and sub.shifts_max > 0:
                effective_requested = min(10, sub.shifts_max)

            if effective_requested > 0:
                effective_requested_by_pid[pid] = effective_requested
                priority = (cfg.priority_weight if cfg and not cfg.casual else 1.0)
                high_span = (effective_requested + 1) // 2
                low_span = effective_requested - high_span

                bonus_high = model.new_int_var(0, high_span, f"reqbonus_hi_{pid}")
                bonus_low = model.new_int_var(0, low_span, f"reqbonus_lo_{pid}")
                model.add(bonus_high + bonus_low <= physician_shift_exprs[pid])
                # 100/50 base weights (vs the old flat 50) — the maximiser
                # will always prefer filling bonus_high before bonus_low
                # since it's worth strictly more per unit.
                deficit_penalty_terms.append(round(100 * priority) * bonus_high)
                if low_span > 0:
                    deficit_penalty_terms.append(round(50 * priority) * bonus_low)
                requested_bonus_by_pid[pid] = bonus_high + bonus_low

            # Small linear term: slight incentive to fill toward max even above requested.
            deficit_penalty_terms.append(3 * physician_shift_exprs[pid])

        # Soft: Group A/B balance — consecutive alternation reward/penalty.
        # Rather than a weak per-shift bonus, we reward A→B or B→A consecutive
        # working pairs (+20) and penalise A→A consecutive pairs (-25).
        # This directly enforces the user requirement: "if 2 shifts in a row,
        # 1 should be Group A, 1 should be Group B."
        #
        # Step 1: create per-(physician, day) BoolVars for working Group A / Group B.
        group_a_bool: dict[tuple, object] = {}  # (pid, d_idx) -> BoolVar
        group_b_bool: dict[tuple, object] = {}
        for pid in pids:
            for d_idx in range(len(all_dates)):
                a_vars = [
                    shifts[(pid, d_idx, shift.code)]
                    for block in BLOCKS for shift in block
                    if shift.site_group == SiteGroup.A
                ]
                b_vars = [
                    shifts[(pid, d_idx, shift.code)]
                    for block in BLOCKS for shift in block
                    if shift.site_group == SiteGroup.B
                ]
                if a_vars:
                    wa = model.new_bool_var(f"ga_{pid}_{d_idx}")
                    # HC-2 ensures at most one shift per physician per day → sum is 0 or 1
                    model.add(sum(a_vars) == wa)
                    group_a_bool[(pid, d_idx)] = wa
                if b_vars:
                    wb = model.new_bool_var(f"gb_{pid}_{d_idx}")
                    model.add(sum(b_vars) == wb)
                    group_b_bool[(pid, d_idx)] = wb

        # Step 1b: per-physician A-floor bonus.
        # Rewards assigning Group A shifts up to the target A count (~38% of requested).
        # Using a capped IntVar so the marginal value of an A shift drops once the
        # target is met — avoids over-shooting in either direction.
        group_balance_terms = []
        for pid in pids:
            sub = self.submissions[pid]
            eff_req = sub.shifts_requested or min(10, sub.shifts_max)
            target_a = max(1, round(eff_req * self._group_a_target))
            a_total_expr = sum(
                shifts[(pid, d_idx, shift.code)]
                for d_idx in range(len(all_dates))
                for block in BLOCKS
                for shift in block
                if shift.site_group == SiteGroup.A
            )
            a_floor = model.new_int_var(0, target_a, f"afloor_{pid}")
            model.add(a_floor <= a_total_expr)
            group_balance_terms.append(30 * a_floor)

        # Step 1c: group_b_site_preference — small bonus for preferred Group B site shifts.
        # This implements the per-physician within-Group-B site preference from physicians.yaml.
        # Weight is small (+6) so it only influences tie-breaking within equivalent options.
        _B_PREF_SITES: dict[str, frozenset] = {
            "nehc":  frozenset({"NEHC"}),
            "rah":   frozenset({"RAH I side", "RAH F side"}),
            "rah_f": frozenset({"RAH F side"}),
        }
        for pid in pids:
            cfg = _get_cfg(pid)
            if not (cfg and cfg.group_b_site_preference):
                continue
            preferred_sites = _B_PREF_SITES.get(cfg.group_b_site_preference)
            if not preferred_sites:
                continue
            for d_idx in range(len(all_dates)):
                for block in BLOCKS:
                    for shift in block:
                        if shift.site in preferred_sites:
                            group_balance_terms.append(
                                6 * shifts[(pid, d_idx, shift.code)]
                            )

        # Step 2: build objective terms for consecutive pairs
        for pid in pids:
            for d_idx in range(len(all_dates) - 1):
                wa1 = group_a_bool.get((pid, d_idx))
                wb1 = group_b_bool.get((pid, d_idx))
                wa2 = group_a_bool.get((pid, d_idx + 1))
                wb2 = group_b_bool.get((pid, d_idx + 1))

                # Reward A→B alternation
                if wa1 is not None and wb2 is not None:
                    ab = model.new_bool_var(f"ab_{pid}_{d_idx}")
                    model.add_implication(ab, wa1)
                    model.add_implication(ab, wb2)
                    model.add(wa1 + wb2 <= 1 + ab)
                    group_balance_terms.append(20 * ab)

                # Reward B→A alternation
                if wb1 is not None and wa2 is not None:
                    ba = model.new_bool_var(f"ba_{pid}_{d_idx}")
                    model.add_implication(ba, wb1)
                    model.add_implication(ba, wa2)
                    model.add(wb1 + wa2 <= 1 + ba)
                    group_balance_terms.append(20 * ba)

                # Penalise A→A consecutive (prefer alternation)
                if wa1 is not None and wa2 is not None:
                    aa = model.new_bool_var(f"aa_{pid}_{d_idx}")
                    model.add_implication(aa, wa1)
                    model.add_implication(aa, wa2)
                    model.add(wa1 + wa2 <= 1 + aa)
                    group_balance_terms.append(-40 * aa)  # penalty when A follows A

        # Soft: Singleton clustering — bonus for consecutive 2400h nights by the same physician.
        # This encourages the solver to cluster night shifts into runs of 2+ days rather than
        # spreading them as isolated singletons (which is harder on physicians and on the roster).
        # We create a per-(physician, day) BoolVar = 1 iff physician works any 2400h on that day,
        # then add +25 per consecutive-night pair.
        night_bool: dict[tuple, object] = {}
        for pid in pids:
            for d_idx in range(len(all_dates)):
                night_vars_for_day = [
                    shifts[(pid, d_idx, shift.code)]
                    for block in BLOCKS
                    for shift in block
                    if shift.time == "2400h"
                ]
                if night_vars_for_day:
                    nb = model.new_bool_var(f"nb_{pid}_{d_idx}")
                    # HC-2 guarantees at most one shift per physician per day,
                    # so sum(night_vars_for_day) is 0 or 1 — safe to equate with a BoolVar.
                    model.add(sum(night_vars_for_day) == nb)
                    night_bool[(pid, d_idx)] = nb

        # ----------------------------------------------------------------
        # HC-13b: No isolated 2400h nights (hard). Unless a physician has
        # prefer_singleton_nights set, a 2400h shift must have at least one
        # adjacent day (day before or after) that's also a 2400h shift for
        # them — i.e. every night-shift run is 2+ days, never a lone night.
        # This also automatically forces "more than one night this month ->
        # they're adjacent", since a scattered set of isolated nights would
        # each individually violate this per-day check.
        #
        # HC-13c: Never night / day-off / night (hard, ALL physicians,
        # including prefer_singleton_nights ones — a single isolated night
        # is fine for them, but two nights with exactly one empty day
        # between them is not allowed for anyone).
        # ----------------------------------------------------------------
        for pid in pids:
            pid_cfg = _get_cfg(pid)
            if not (pid_cfg and pid_cfg.prefer_singleton_nights):
                for d_idx in range(len(all_dates)):
                    curr = night_bool.get((pid, d_idx))
                    if curr is None:
                        continue
                    neighbors = [
                        night_bool[(pid, d_idx + off)]
                        for off in (-1, 1)
                        if (pid, d_idx + off) in night_bool
                    ]
                    if neighbors:
                        model.add(curr <= sum(neighbors))

            for d_idx in range(len(all_dates) - 2):
                d0 = night_bool.get((pid, d_idx))
                d1 = night_bool.get((pid, d_idx + 1))
                d2 = night_bool.get((pid, d_idx + 2))
                if d0 is not None and d1 is not None and d2 is not None:
                    model.add(d0 + d2 <= 1 + d1)

        clustering_bonus_terms = []
        for pid in pids:
            # Skip clustering bonus for physicians who prefer singleton nights.
            pid_cfg = _get_cfg(pid)
            if pid_cfg and pid_cfg.prefer_singleton_nights:
                continue
            for d_idx in range(len(all_dates) - 1):
                nb1 = night_bool.get((pid, d_idx))
                nb2 = night_bool.get((pid, d_idx + 1))
                if nb1 is None or nb2 is None:
                    continue
                consec = model.new_bool_var(f"cnight_{pid}_{d_idx}")
                # consec == (nb1 AND nb2): standard linearization
                model.add_implication(consec, nb1)
                model.add_implication(consec, nb2)
                # If both are 1 the solver must set consec=1 (since we're maximizing)
                model.add(nb1 + nb2 <= 1 + consec)
                clustering_bonus_terms.append(18 * consec)

        # Anti-clustering penalty for prefer_singleton_nights physicians.
        # These physicians want isolated 2400h shifts, so consecutive nights are penalised.
        for pid in pids:
            pid_cfg = _get_cfg(pid)
            if not (pid_cfg and pid_cfg.prefer_singleton_nights):
                continue
            for d_idx in range(len(all_dates) - 1):
                nb1 = night_bool.get((pid, d_idx))
                nb2 = night_bool.get((pid, d_idx + 1))
                if nb1 is None or nb2 is None:
                    continue
                consec = model.new_bool_var(f"anti_cnight_{pid}_{d_idx}")
                model.add_implication(consec, nb1)
                model.add_implication(consec, nb2)
                model.add(nb1 + nb2 <= 1 + consec)
                clustering_bonus_terms.append(-30 * consec)  # penalty for consecutive nights

        # Soft: Any-shift clustering bonus — reward consecutive working days.
        # Mirrors the 2400h singleton logic but for all shift types: a bonus for
        # each adjacent (day, day+1) pair where the physician works both days.
        # This discourages isolated single-day assignments across the whole roster.
        # Weight is kept below the 2400h bonus (12) since nights cluster more tightly.
        any_cluster_terms = []
        for pid in pids:
            # Note: prefer_singleton_nights physicians are NOT excluded here.
            # The anti-clustering penalty above handles their nights; we still
            # want to reward consecutive day shifts for them.
            for d_idx in range(len(all_dates) - 1):
                wb1 = worked_bool.get((pid, d_idx))
                wb2 = worked_bool.get((pid, d_idx + 1))
                if wb1 is None or wb2 is None:
                    continue
                consec = model.new_bool_var(f"consec_any_{pid}_{d_idx}")
                model.add_implication(consec, wb1)
                model.add_implication(consec, wb2)
                model.add(wb1 + wb2 <= 1 + consec)
                any_cluster_terms.append(10 * consec)

        # ----------------------------------------------------------------
        # HC-11: Late-shift rest privilege (rest_after_late_shift)
        # If a physician works a 1600h, 1800h, or 2000h shift on day d,
        # they must have day d+1 off entirely.
        # ----------------------------------------------------------------
        _LATE_TIMES = {"1600h", "1800h", "2000h"}
        for pid in pids:
            cfg = _get_cfg(pid)
            if not (cfg and cfg.rest_after_late_shift):
                continue
            for d_idx in range(len(all_dates) - 1):
                for block in BLOCKS:
                    for shift in block:
                        if shift.time not in _LATE_TIMES:
                            continue
                        late_var = shifts[(pid, d_idx, shift.code)]
                        for block2 in BLOCKS:
                            for shift2 in block2:
                                model.add_implication(
                                    late_var,
                                    shifts[(pid, d_idx + 1, shift2.code)].negated()
                                )

        # ----------------------------------------------------------------
        # HC-12: Max consecutive days with 1800h shift
        # ----------------------------------------------------------------
        for pid in pids:
            cfg = _get_cfg(pid)
            mc1800 = cfg.max_consecutive_1800h if cfg else 3
            if mc1800 >= 3:
                continue  # default: no effective constraint
            win = mc1800 + 1
            for start in range(len(all_dates) - mc1800):
                window_vars = [
                    shifts[(pid, d_idx, shift.code)]
                    for d_idx in range(start, start + win)
                    for block in BLOCKS
                    for shift in block
                    if shift.time == "1800h"
                ]
                if window_vars:
                    model.add(sum(window_vars) <= mc1800)

        # ----------------------------------------------------------------
        # HC-12b: Post-block cool-down (post_block_rest_days)
        # If a physician works a stretch of >= post_block_min_length
        # consecutive days that is immediately followed by a day off (i.e.
        # this is genuinely where the stretch ends, not a sub-window of a
        # longer one), the following post_block_rest_days days must be
        # entirely off too. Triggered on the true end of a run — not on
        # every min_length sub-window of a longer run — by requiring the
        # day right after the window to already be off as part of the same
        # AND condition, so a 3-day block with post_block_min_length=2
        # can't have its own middle day contradictorily forced off by an
        # earlier sub-window firing prematurely.
        # ----------------------------------------------------------------
        for pid in pids:
            cfg = _get_cfg(pid)
            rest_days = cfg.post_block_rest_days if cfg else 0
            if not rest_days:
                continue
            min_len = cfg.post_block_min_length if cfg else 2
            if min_len < 1 or min_len > len(all_dates):
                continue
            for end in range(min_len - 1, len(all_dates)):
                next_idx = end + 1
                if next_idx >= len(all_dates):
                    break  # no next day within this month to check/require off
                window = [worked_bool[(pid, d_idx)] for d_idx in range(end - min_len + 1, end + 1)]
                literals = window + [worked_bool[(pid, next_idx)].negated()]
                run_end = model.new_bool_var(f"blockend_{pid}_{end}")
                # Forces run_end=1 only when every literal is true (full
                # min_len window worked AND the day right after is off) —
                # solver has no incentive to set it spuriously since doing
                # so only adds constraints below, never helps the objective.
                model.add(sum(literals) <= len(literals) - 1 + run_end)
                for k in range(rest_days):
                    rest_idx = next_idx + k
                    if rest_idx >= len(all_dates):
                        break
                    model.add_implication(run_end, worked_bool[(pid, rest_idx)].negated())

        # ----------------------------------------------------------------
        # HC-12c: No more than N consecutive days at the same site
        # (max_consecutive_same_site) — "no 2 Intake/NEHC shifts in a row".
        # Mirrors HC-12's 1800h-cap sliding-window pattern, filtered by
        # site instead of shift time.
        # ----------------------------------------------------------------
        for pid in pids:
            cfg = _get_cfg(pid)
            max_same_site = cfg.max_consecutive_same_site if cfg else None
            if not max_same_site:
                continue
            window = max_same_site + 1
            if window > len(all_dates):
                continue
            for site_name in _ALL_SITES:
                for start in range(len(all_dates) - max_same_site):
                    window_vars = [
                        shifts[(pid, d_idx, shift.code)]
                        for d_idx in range(start, start + window)
                        for block in BLOCKS
                        for shift in block
                        if shift.site == site_name
                    ]
                    if window_vars:
                        model.add(sum(window_vars) <= max_same_site)

        # Soft penalty for 4-consecutive-day runs.
        # Physicians with max_consecutive_shifts >= 4 are ALLOWED to work 4 days in a
        # row (hard constraint), but we discourage it as a last resort.
        # Physicians with mc < 4 already cannot (HC-8), so skip them.
        run_penalty_terms = []
        for pid in pids:
            if _max_consec(pid) < 4:
                continue
            for d_idx in range(len(all_dates) - 3):
                w0 = worked_bool.get((pid, d_idx))
                w1 = worked_bool.get((pid, d_idx + 1))
                w2 = worked_bool.get((pid, d_idx + 2))
                w3 = worked_bool.get((pid, d_idx + 3))
                if w0 is None or w1 is None or w2 is None or w3 is None:
                    continue
                run4 = model.new_bool_var(f"run4_{pid}_{d_idx}")
                model.add_implication(run4, w0)
                model.add_implication(run4, w1)
                model.add_implication(run4, w2)
                model.add_implication(run4, w3)
                model.add(w0 + w1 + w2 + w3 <= 3 + run4)
                run_penalty_terms.append(-35 * run4)

        # Soft: avoid-weekday penalty (-5 per shift on the physician's
        # avoided weekday, e.g. a protected admin day). Generalizes the
        # legacy avoid_mondays flag (still honoured as shorthand for
        # avoid_weekday="MON" when avoid_weekday itself is unset — see
        # PhysicianConfig.avoid_weekday) to any single weekday.
        _WEEKDAY_NUM = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
        monday_penalty_terms = []
        for pid in pids:
            cfg = _get_cfg(pid)
            target_day = None
            if cfg:
                if cfg.avoid_weekday:
                    target_day = _WEEKDAY_NUM.get(cfg.avoid_weekday)
                elif cfg.avoid_mondays:
                    target_day = 0
            if target_day is None:
                continue
            for d_idx, d in enumerate(all_dates):
                if d.weekday() != target_day:
                    continue
                for block in BLOCKS:
                    for shift in block:
                        monday_penalty_terms.append(-5 * shifts[(pid, d_idx, shift.code)])

        # Composite objective (all terms are non-negative rewards; maximize)
        # filled_expr is already per-shift-weighted (see above) — do not
        # multiply by 1000 again here.
        objective_terms = [filled_expr]
        objective_terms.extend(request_bonus_terms)
        objective_terms.extend(deficit_penalty_terms)
        objective_terms.extend(group_balance_terms)
        objective_terms.extend(clustering_bonus_terms)
        objective_terms.extend(any_cluster_terms)
        objective_terms.extend(run_penalty_terms)
        objective_terms.extend(monday_penalty_terms)
        objective_terms.extend(long_gap_penalty_terms)
        objective_terms.extend(swing_penalty_terms)
        objective_terms.extend(anchor_overage_penalty_terms)
        objective_terms.extend(anchor_fulfillment_bonus_terms)
        objective_terms.extend(weekend_clump_penalty_terms)

        # ----------------------------------------------------------------
        # Lexicographic casual-priority tiers (only when casual physicians
        # exist in this roster). Everything above — every hard constraint,
        # every decision variable, HC-7's casual hard-cap — is already built
        # into this ONE model for every physician. Each tier just picks a
        # different objective to maximize on that same model, solves, and
        # then locks in the achieved value with model.add(...) before the
        # next tier runs, so a later tier can never undo an earlier one's
        # guarantee. This is what makes the priority order (1) normal
        # physicians to their requested count, (2) casual physicians to
        # their requested count, (3) normal physicians up to their real
        # max — a guarantee rather than just a weighted preference, without
        # ever losing sight of the full problem (unlike three separate
        # models, which would have no way to know what another phase
        # already assigned).
        # ----------------------------------------------------------------
        casual_pids = {pid for pid in pids if getattr(_get_cfg(pid), "casual", False)}
        normal_bonus_terms = [
            requested_bonus_by_pid[pid] for pid in pids
            if pid not in casual_pids and pid in requested_bonus_by_pid
        ]
        casual_bonus_terms = [
            requested_bonus_by_pid[pid] for pid in pids
            if pid in casual_pids and pid in requested_bonus_by_pid
        ]

        # Tiers 1/2 get a small slice of the total budget, not a share on
        # top of tier 3 getting the full time_limit again — otherwise the
        # actual wall-clock time can run to ~1.7x what the caller asked
        # for (and what the UI's countdown displays), since each tier
        # used to get its own independent allocation. Whatever tiers 1/2
        # actually spend (via .wall_time, not the cap itself) is deducted
        # from tier 3's budget, so total time stays close to time_limit.
        remaining_time_limit = time_limit

        def _apply_hint(cb: "_SolutionCallback") -> None:
            # Warm-start the next solve from this tier's actual found
            # solution, so it only has to improve on a known-feasible
            # point instead of rediscovering feasibility from scratch.
            # Without this, each tier's fresh CpSolver() cold-starts with
            # no memory of the previous tier's search, which can make an
            # otherwise easy (or even looser/more relaxed) problem take an
            # unpredictable, sometimes very long time to find anything at
            # all — a search-quality artifact, not a feasibility one.
            if not cb.best_values:
                return
            model.clear_hints()
            for k, v in cb.best_values.items():
                model.add_hint(shifts[k], v)

        if casual_pids and casual_bonus_terms:
            tier_time_limit = min(60.0, max(5.0, time_limit * 0.1))

            if normal_bonus_terms:
                logger.info("CP-SAT lexicographic: tier 1/3 — normal physicians toward requested")
                tier1_expr = sum(normal_bonus_terms)
                model.maximize(tier1_expr)
                tier1_solver = _cp_model.CpSolver()
                tier1_solver.parameters.max_time_in_seconds = tier_time_limit
                tier1_solver.parameters.num_search_workers = num_workers
                tier1_cb = _SolutionCallback(shifts, should_stop=cancel_check)
                tier1_status = tier1_solver.solve(model, tier1_cb)
                if tier1_status in (_cp_model.OPTIMAL, _cp_model.FEASIBLE) and tier1_cb.best_values:
                    model.add(tier1_expr >= int(tier1_cb.best_objective))
                    # Also freeze each individual physician's achieved count,
                    # not just the tier-wide sum. Locking only the sum lets a
                    # later tier trade one physician's progress for another's
                    # of equal weight — e.g. dropping someone a shift below
                    # their own request to buy a group-balance or clustering
                    # bonus elsewhere, since the total normal_bonus_terms sum
                    # is unchanged either way. Confirmed via a real run: Keyes
                    # landed a shift under his own stated request even though
                    # tier1's sum-only lock was respected. Capped at each
                    # physician's own effective_requested so this can never
                    # ratchet in an accidental tier1 overage beyond what they
                    # actually asked for.
                    for pid, eff_req in effective_requested_by_pid.items():
                        if pid in casual_pids:
                            continue
                        achieved = sum(
                            tier1_cb.best_values.get((pid, d_idx, shift.code), 0)
                            for d_idx in range(len(all_dates))
                            for block in BLOCKS
                            for shift in block
                        )
                        floor = min(achieved, eff_req)
                        if floor > 0:
                            model.add(physician_shift_exprs[pid] >= floor)
                    _apply_hint(tier1_cb)
                remaining_time_limit -= tier1_solver.wall_time
                if progress_callback:
                    progress_callback(60, 100, tier1_cb.best_objective if tier1_cb.best_values else 0.0)

            logger.info("CP-SAT lexicographic: tier 2/3 — casual physicians toward requested")
            tier2_expr = sum(casual_bonus_terms)
            model.maximize(tier2_expr)
            tier2_solver = _cp_model.CpSolver()
            tier2_solver.parameters.max_time_in_seconds = tier_time_limit
            tier2_solver.parameters.num_search_workers = num_workers
            tier2_cb = _SolutionCallback(shifts, should_stop=cancel_check)
            tier2_status = tier2_solver.solve(model, tier2_cb)
            if tier2_status in (_cp_model.OPTIMAL, _cp_model.FEASIBLE) and tier2_cb.best_values:
                model.add(tier2_expr >= int(tier2_cb.best_objective))
                _apply_hint(tier2_cb)
            remaining_time_limit -= tier2_solver.wall_time
            if progress_callback:
                progress_callback(75, 100, tier2_cb.best_objective if tier2_cb.best_values else 0.0)

            # Floor so tier 3 — the tier that actually matters most for
            # schedule quality — always gets a meaningful budget even if
            # tiers 1/2 ate most of their (small) allocations.
            remaining_time_limit = max(30.0, remaining_time_limit)
            logger.info("CP-SAT lexicographic: tier 3/3 — full objective (normal physicians up to max)")

        # ----------------------------------------------------------------
        # Anchor-fulfillment floor (2400h/0600h requests). Runs
        # unconditionally — unlike tiers 1/2 above, not gated on casual
        # physicians existing — since the problem it fixes has nothing to
        # do with casual staffing. Confirmed on real October 2026 data: a
        # 20-minute solve at 1.1% optimality gap (so not a search-depth
        # problem) gave one physician 0 of 6 explicitly requested 2400h
        # shifts, with genuine availability, while shifts went to others
        # who never asked for any.
        #
        # First implementation here was a single tier maximizing the SUM
        # of anchor-fulfillment across everyone — re-tested against the
        # same real data and it did NOT fix the problem (RScheirer stayed
        # at 0/6). Root cause: a pure sum is fairness-blind — it's exactly
        # as "optimal" whether the total is spread across many physicians
        # or concentrated on a few, since nothing in a flat sum objective
        # prefers breadth over depth. So this is genuinely two stages, not
        # one:
        #   Stage A (breadth): maximize the COUNT of (physician, anchor
        #     type) pairs that get AT LEAST ONE shift of their requested
        #     type — a boolean "got_var" per pair, reified off the same
        #     sum-of-type-vars each fill_2400/fill_0600 already uses.
        #     This is what actually stops anyone being left at a hard
        #     zero when giving them even one is feasible.
        #   Stage B (depth): THEN maximize total units (the original sum),
        #     on top of stage A's breadth guarantee, to use up whatever
        #     capacity remains as efficiently as possible.
        #
        # Runs after tiers 1/2 (if they ran) deliberately — reaching a
        # physician's own requested TOTAL shift count should take priority
        # over the TYPE mix within that count; you can't fulfill an anchor
        # request with a shift you were never assigned in the first place.
        #
        # Soft throughout, never a hard model.add(>= requested) constraint:
        # a physician whose roster-level only_0600h/only_2400h flag
        # conflicts with a stale monthly request for the type they can't
        # work must degrade gracefully (HC-5 already forces their
        # vars_2400/vars_0600 for that type to 0, so their achievable
        # ceiling here is naturally 0) rather than make the whole model
        # infeasible over one inconsistent data row.
        #
        # Both stages lock each individual physician's achieved count per
        # anchor type, not just the stage-wide sum/count — a sum-only lock
        # would let a later stage (or the final tier) trade one
        # physician's fulfillment for another's of equal weight, which is
        # exactly the failure mode this exists to close (same reasoning as
        # tier 1's own per-physician lock above). Recomputed from the
        # shift assignments directly rather than read off the
        # fill_2400/fill_0600/got_* variables themselves, since the
        # solution callback only tracks `shifts` vars.
        # ----------------------------------------------------------------
        def _lock_anchor_floors(best_values: dict) -> None:
            for pid, requests in anchor_requests_by_pid.items():
                for time_str, requested in requests:
                    achieved_count = sum(
                        best_values.get((pid, d_idx, shift.code), 0)
                        for d_idx in range(len(all_dates))
                        for block in BLOCKS
                        for shift in block
                        if shift.time == time_str
                    )
                    floor = min(achieved_count, requested)
                    if floor > 0:
                        type_vars = [
                            shifts[(pid, d_idx, shift.code)]
                            for d_idx in range(len(all_dates))
                            for block in BLOCKS
                            for shift in block
                            if shift.time == time_str
                        ]
                        model.add(sum(type_vars) >= floor)

        if anchor_fill_vars_flat:
            anchor_tier_time_limit = min(60.0, max(5.0, time_limit * 0.1))

            # Stage A — breadth: nobody with a real request gets left at zero.
            logger.info("CP-SAT lexicographic: anchor-fulfillment floor, stage A (breadth)")
            got_vars_flat = []
            for pid, requests in anchor_requests_by_pid.items():
                for time_str, _requested in requests:
                    type_vars = [
                        shifts[(pid, d_idx, shift.code)]
                        for d_idx in range(len(all_dates))
                        for block in BLOCKS
                        for shift in block
                        if shift.time == time_str
                    ]
                    got = model.new_bool_var(f"anchorgot_{time_str}_{pid}")
                    model.add(got <= sum(type_vars))
                    got_vars_flat.append(got)

            stageA_expr = sum(got_vars_flat)
            model.maximize(stageA_expr)
            stageA_solver = _cp_model.CpSolver()
            stageA_solver.parameters.max_time_in_seconds = anchor_tier_time_limit
            stageA_solver.parameters.num_search_workers = num_workers
            stageA_cb = _SolutionCallback(shifts, should_stop=cancel_check)
            stageA_status = stageA_solver.solve(model, stageA_cb)
            if stageA_status in (_cp_model.OPTIMAL, _cp_model.FEASIBLE) and stageA_cb.best_values:
                model.add(stageA_expr >= int(stageA_cb.best_objective))
                _lock_anchor_floors(stageA_cb.best_values)
                _apply_hint(stageA_cb)
            remaining_time_limit -= stageA_solver.wall_time
            if progress_callback:
                progress_callback(80, 100, stageA_cb.best_objective if stageA_cb.best_values else 0.0)

            # Stage B — depth: maximize total units on top of stage A's floor.
            logger.info("CP-SAT lexicographic: anchor-fulfillment floor, stage B (depth)")
            stageB_expr = sum(anchor_fill_vars_flat)
            model.maximize(stageB_expr)
            stageB_solver = _cp_model.CpSolver()
            stageB_solver.parameters.max_time_in_seconds = anchor_tier_time_limit
            stageB_solver.parameters.num_search_workers = num_workers
            stageB_cb = _SolutionCallback(shifts, should_stop=cancel_check)
            stageB_status = stageB_solver.solve(model, stageB_cb)
            if stageB_status in (_cp_model.OPTIMAL, _cp_model.FEASIBLE) and stageB_cb.best_values:
                model.add(stageB_expr >= int(stageB_cb.best_objective))
                _lock_anchor_floors(stageB_cb.best_values)
                _apply_hint(stageB_cb)
            remaining_time_limit -= stageB_solver.wall_time
            remaining_time_limit = max(30.0, remaining_time_limit)
            if progress_callback:
                progress_callback(85, 100, stageB_cb.best_objective if stageB_cb.best_values else 0.0)

        model.maximize(sum(objective_terms))

        # ----------------------------------------------------------------
        # Solve
        # ----------------------------------------------------------------
        solver = _cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = remaining_time_limit
        solver.parameters.num_search_workers = num_workers
        solver.parameters.log_search_progress = True  # TODO: disable after diagnosis

        # Solution callback: saves variable values on each improving solution so
        # we use the saved dict in _build_result instead of calling solver.value()
        # after solve() returns (avoids potential thread-state issues in frozen binaries).
        solution_cb = _SolutionCallback(shifts, should_stop=cancel_check)

        logger.info(
            "CP-SAT: starting solve for %d-%02d with time_limit=%.0fs, workers=%d",
            year, month, time_limit, num_workers,
        )
        status = solver.solve(model, solution_cb)
        wall_time = solver.wall_time
        logger.info(
            "CP-SAT: status=%s  objective=%.0f  wall_time=%.1fs",
            solver.status_name(status),
            solution_cb.best_objective if solution_cb.best_values else 0.0,
            wall_time,
        )

        if progress_callback:
            # best_objective stays at its -inf default when the solve found
            # zero improving solutions in its time budget (on_solution_callback
            # never fired) — report 0.0 rather than passing inf through to a
            # caller that may round()/int() it (e.g. server.py's progress_cb).
            reported_score = -solution_cb.best_objective if solution_cb.best_values else 0.0
            progress_callback(100, 100, reported_score)

        # ----------------------------------------------------------------
        # Extract solution
        # ----------------------------------------------------------------
        if status in (_cp_model.OPTIMAL, _cp_model.FEASIBLE) and solution_cb.best_values:
            result = self._build_result(
                year, month, all_dates, solution_cb.best_values, shift_by_code
            )
            # Attach solver quality info to stats
            if result.stats:
                is_optimal = status == _cp_model.OPTIMAL
                obj = solution_cb.best_objective
                bound = solution_cb.best_bound
                if is_optimal or abs(bound) < 1e-6:
                    gap_pct = 0.0
                else:
                    gap_pct = max(0.0, (bound - obj) / max(abs(bound), 1.0) * 100.0)
                result.stats.solver_status = "optimal" if is_optimal else "feasible"
                result.stats.optimality_gap_pct = round(gap_pct, 2)
                logger.info(
                    "CP-SAT quality: status=%s  obj=%.0f  bound=%.0f  gap=%.2f%%",
                    result.stats.solver_status, obj, bound, gap_pct,
                )
            return result

        # No feasible solution found — return empty result with all slots unfilled
        logger.warning("CP-SAT: no feasible solution found (status=%s)", solver.status_name(status))
        result = ScheduleResult(year=year, month=month)
        all_shifts_obj = _all_shifts()
        # Rebuild a minimal state for near-miss candidates
        self._pid_to_slots: dict[str, list] = defaultdict(list)
        self._slot_to_pid: dict[tuple, str] = {}
        self._shift_count: dict[str, int] = defaultdict(int)
        self._anchor_count: dict[str, int] = defaultdict(int)
        self._weekend_keys: dict[str, set] = defaultdict(set)
        for d in all_dates:
            for shift in all_shifts_obj:
                candidates = self._near_miss_candidates(d, shift)
                result.unfilled.append(UnfilledSlot(date=d, shift=shift, candidates=candidates))
                result.issues.append(f"{d.strftime('%b %d')} {shift.code}: no eligible physician")
        result.stats = self._compute_stats(result)
        return result

    # ------------------------------------------------------------------
    # Post-solve result construction
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Mutable-state helpers (mirrors ScheduleGenerator for post-generation use)
    # ------------------------------------------------------------------

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
        self._pid_to_slots[pid] = [
            (ad, s) for ad, s in self._pid_to_slots[pid]
            if not (ad == d and s.code == shift.code)
        ]
        self._shift_count[pid] = max(0, self._shift_count[pid] - 1)
        if shift.time in ("0600h", "2400h"):
            self._anchor_count[pid] = max(0, self._anchor_count[pid] - 1)
        if d.weekday() in _WEEKEND_WEEKDAYS:
            self._weekend_keys[pid] = {
                _weekend_key(ad) for ad, _ in self._pid_to_slots[pid]
                if ad.weekday() in _WEEKEND_WEEKDAYS
            }

    def _check_constraints(
        self, pid: str, d: datetime.date, shift: Shift, block_idx: int | None = None
    ) -> list[ViolationReason] | None:
        """Constraint check for post-generation use (check-violations endpoint)."""
        if block_idx is None:
            block_idx = SHIFT_TO_BLOCK[shift.code]
        violations = self._check_constraints_simple(pid, d, shift, block_idx)
        return violations if violations else None

    def assign_manual(
        self,
        physician_id: str,
        d: datetime.date,
        shift: Shift,
    ) -> list[ViolationReason]:
        """Force-assign physician_id to (d, shift), returning any rule violations."""
        violations = self._check_constraints(physician_id, d, shift) or []
        self._assign(physician_id, d, shift)
        return violations

    def _build_result(
        self,
        year: int,
        month: int,
        all_dates: list[datetime.date],
        saved_values: dict,
        shift_by_code: dict[str, Shift],
    ) -> ScheduleResult:
        """Convert CP-SAT solution values into a ScheduleResult."""
        result = ScheduleResult(year=year, month=month)
        pids = list(self.submissions.keys())

        # Rebuild mutable state for near-miss candidate computation
        self._pid_to_slots: dict[str, list] = defaultdict(list)
        self._slot_to_pid: dict[tuple, str] = {}
        self._shift_count: dict[str, int] = defaultdict(int)
        self._anchor_count: dict[str, int] = defaultdict(int)
        self._weekend_keys: dict[str, set] = defaultdict(set)

        for d_idx, d in enumerate(all_dates):
            for block in BLOCKS:
                for shift in block:
                    assigned_pid = None
                    for pid in pids:
                        val = saved_values.get((pid, d_idx, shift.code), 0)
                        if val:
                            assigned_pid = pid
                            break
                    if assigned_pid:
                        self._slot_to_pid[(d, shift.code)] = assigned_pid
                        self._pid_to_slots[assigned_pid].append((d, shift))
                        self._shift_count[assigned_pid] += 1
                        if shift.time in ("0600h", "2400h"):
                            self._anchor_count[assigned_pid] += 1
                        if d.weekday() in _WEEKEND_WEEKDAYS:
                            self._weekend_keys[assigned_pid].add(_weekend_key(d))
                        result.assignments.append(Assignment(
                            date=d,
                            shift=shift,
                            physician_id=assigned_pid,
                            physician_name=self.submissions[assigned_pid].physician_name,
                        ))
                    else:
                        candidates = self._near_miss_candidates(d, shift)
                        result.unfilled.append(
                            UnfilledSlot(date=d, shift=shift, candidates=candidates)
                        )
                        result.issues.append(
                            f"{d.strftime('%b %d')} {shift.code}: no eligible physician"
                        )

        result.stats = self._compute_stats(result)
        return result

    # ------------------------------------------------------------------
    # Near-miss candidates (adapted from ScheduleGenerator)
    # ------------------------------------------------------------------

    def _near_miss_candidates(
        self, d: datetime.date, shift: Shift, max_n: int = 10
    ) -> list[CandidateOption]:
        # Hard disqualifiers (physician never shown as a candidate):
        #   1. Already assigned a shift on this day
        #   2. Another shift start within the minimum spacing window (default 22h)
        #   3. Marked unavailable for this day
        # Everything else (site restrictions, max shifts, etc.) is a soft warning.
        min_spacing = self.config.get('spacing', {}).get('min_hours_between_shifts', 22)
        block_idx = SHIFT_TO_BLOCK[shift.code]
        _SHIFT_HOUR = {"0600h": 6, "1000h": 10, "1700h": 17, "1800h": 18, "2400h": 24}
        _EPOCH = datetime.date(2000, 1, 1)
        proposed_abs = (d - _EPOCH).days * 24 + _SHIFT_HOUR.get(shift.time, 0)
        results = []
        for pid, sub in self.submissions.items():
            pid_slots = self._pid_to_slots.get(pid, [])
            # Hard block 1: already working this day
            if any(ad == d for ad, _ in pid_slots):
                continue
            # Hard block 2: marked unavailable for this day
            day_avail = next((day for day in sub.days if day.date == d), None)
            if day_avail is None or not day_avail.wants_to_work:
                continue
            # Hard block 3: another shift within minimum spacing
            too_close = False
            for existing_date, existing_shift in pid_slots:
                existing_abs = (existing_date - _EPOCH).days * 24 + _SHIFT_HOUR.get(existing_shift.time, 0)
                if abs(proposed_abs - existing_abs) < min_spacing:
                    too_close = True
                    break
            if too_close:
                continue
            # Soft violations — shown as warnings but do not block the candidate
            violations = self._check_constraints_simple(pid, d, shift, block_idx)
            fit = self._near_miss_score(pid, d, shift)
            deficit = max(0, sub.shifts_requested - self._shift_count.get(pid, 0))
            results.append((len(violations), -fit, -deficit, pid, violations))
        results.sort()
        return [
            CandidateOption(
                physician_id=pid,
                physician_name=self.submissions[pid].physician_name,
                violations=violations,
            )
            for _, _, _, pid, violations in results[:max_n]
        ]

    # ------------------------------------------------------------------
    # Post-generation slot helpers (ported from ScheduleGenerator — same
    # self._pid_to_slots[pid]: list[(date, Shift)] structure in both).
    # ------------------------------------------------------------------

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
        future = [(d, s) for d, s in self._pid_to_slots[pid] if d > after_date]
        if not future:
            return None
        earliest_d, earliest_s = min(future, key=lambda x: x[0])
        return earliest_s, earliest_d

    def _run_length_ending_before(self, pid: str, new_date: datetime.date) -> int:
        assigned = {d for d, _ in self._pid_to_slots[pid]}
        count = 0
        check = new_date - datetime.timedelta(days=1)
        while check in assigned:
            count += 1
            check -= datetime.timedelta(days=1)
        return count

    def _run_length_starting_after(self, pid: str, new_date: datetime.date) -> int:
        assigned = {d for d, _ in self._pid_to_slots[pid]}
        count = 0
        check = new_date + datetime.timedelta(days=1)
        while check in assigned:
            count += 1
            check += datetime.timedelta(days=1)
        return count

    def _night_run_ending_before(self, pid: str, d: datetime.date) -> int:
        night_dates = {ad for ad, s in self._pid_to_slots[pid] if s.time == "2400h"}
        count = 0
        check = d - datetime.timedelta(days=1)
        while check in night_dates:
            count += 1
            check -= datetime.timedelta(days=1)
        return count

    def _check_constraints_simple(
        self,
        pid: str,
        d: datetime.date,
        shift: Shift,
        block_idx: int,
    ) -> list[ViolationReason]:
        """
        Constraint check for both near-miss candidate ranking AND the
        post-generation manual-assign / check-violations endpoints (via
        _check_constraints above) — despite the name, this is NOT allowed
        to skip anything in _HARD_VIOLATION_RULES that a real assignment
        could hit, or a manual reassignment on a CP-SAT-generated schedule
        silently slips past rules the UI claims to enforce (this happened:
        a manual swap onto a 4th consecutive day for a physician capped at
        2 produced no warning at all, because this method used to check
        only 6 of the ~10 hard rules — availability/site/max-shifts but
        not consecutive-day, rest-spacing, or night-run limits).
        """
        v: list[ViolationReason] = []
        sub = self.submissions[pid]
        cfg = (
            self.roster.get(pid)
            or self._roster_lower.get(pid.lower())
            or self._roster_by_name.get(pid.lower())
        )

        day_specific = self._shift_avail.get((pid, d))
        if (pid, d, block_idx) not in self._avail:
            # For flat-file imports, a specific shift in this block is still valid
            # even if the full block isn't in _avail.
            if day_specific is None or shift.code not in day_specific:
                v.append(ViolationReason(rule="availability", description=f"Not available for block {block_idx} on {d}"))
        elif day_specific is not None and shift.code not in day_specific:
            v.append(ViolationReason(rule="shift_not_available", description=f"Only available for: {', '.join(sorted(day_specific))}"))

        forbidden = cfg.forbidden_sites if cfg else []
        if shift.site in forbidden:
            v.append(ViolationReason(rule="forbidden_site", description=f"{shift.site} is a forbidden site"))

        if cfg and cfg.only_2400h and shift.time != "2400h":
            v.append(ViolationReason(rule="shift_type_restriction", description=f"Restricted to 2400h shifts only"))

        if cfg and cfg.only_0600h and shift.time != "0600h":
            v.append(ViolationReason(rule="shift_type_restriction", description=f"Restricted to 0600h shifts only"))

        if cfg and shift.time in cfg.forbidden_shift_times:
            v.append(ViolationReason(rule="shift_type_restriction", description=f"{cfg.name} cannot work {shift.time} shifts"))

        pid_slots = self._pid_to_slots.get(pid, [])
        if any(ad == d for ad, _ in pid_slots):
            v.append(ViolationReason(rule="already_assigned_today", description=f"Already has a shift on {d}"))

        hard_max = sub.shifts_max if sub.shifts_max > 0 else sub.shifts_requested
        if self._shift_count.get(pid, 0) >= hard_max > 0:
            v.append(ViolationReason(rule="max_shifts", description=f"Already at maximum shifts ({hard_max})"))

        # Consecutive day limit — bidirectional so it's correct regardless
        # of whether earlier or later dates were assigned first.
        max_consec = cfg.max_consecutive_shifts if cfg else self._max_consec_default
        run_before = self._run_length_ending_before(pid, d)
        run_after = self._run_length_starting_after(pid, d)
        total_run = run_before + 1 + run_after
        if total_run > max_consec:
            v.append(ViolationReason(
                rule="consecutive_limit",
                description=f"Would create a {total_run}-day consecutive run (limit {max_consec})",
            ))

        # Spacing: 23h minimum / post-2400h rest — checked bidirectionally.
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
                            f"Only {actual_h}h gap: {prev_shift.time} on {prev_date:%b %d} "
                            f"→ {shift.time} on {d:%b %d} (need 23h)"
                        ),
                    ))
        nxt = self._next_assigned(pid, d)
        if nxt:
            nxt_shift, nxt_date = nxt
            fwd_gap = (nxt_date - d).days
            if not is_next_shift_ok(shift, fwd_gap, nxt_shift):
                if shift.time == "2400h" and fwd_gap == 2:
                    v.append(ViolationReason(
                        rule="post_2400h_rest",
                        description=(
                            f"After 2400h on {d:%b %d}, next shift must start at noon "
                            f"or later (have: {nxt_shift.time} on {nxt_date:%b %d})"
                        ),
                    ))
                else:
                    actual_h = (nxt_shift.start_hour + fwd_gap * 24) - shift.start_hour
                    v.append(ViolationReason(
                        rule="spacing_23h",
                        description=(
                            f"Only {actual_h}h gap: {shift.time} on {d:%b %d} "
                            f"→ {nxt_shift.time} on {nxt_date:%b %d} (need 23h)"
                        ),
                    ))

        # NIAR — max consecutive 2400h (overnight) shifts.
        if shift.time == "2400h":
            max_nights = cfg.max_consecutive_nights if cfg else self._max_consec_default
            night_run = self._night_run_ending_before(pid, d)
            if night_run >= max_nights:
                v.append(ViolationReason(
                    rule="niar_limit",
                    description=f"Would extend consecutive night run to {night_run + 1} (NIAR limit {max_nights})",
                ))

        return v

    def _near_miss_score(self, pid: str, d: datetime.date, shift: Shift) -> float:
        sub = self.submissions[pid]
        block_idx = SHIFT_TO_BLOCK[shift.code]
        score = 0.0
        if (pid, d, block_idx) in self._avail:
            score += 3.0
        cfg = self.roster.get(pid) or self._roster_lower.get(pid.lower())
        forbidden = cfg.forbidden_sites if cfg else []
        if shift.site not in forbidden:
            score += 2.0
        if self._shift_count.get(pid, 0) < sub.shifts_requested:
            score += 1.0
        return score

    # ------------------------------------------------------------------
    # Stats (identical logic to ScheduleGenerator._compute_stats)
    # ------------------------------------------------------------------

    def _compute_stats(self, result: ScheduleResult) -> ScheduleStats:
        filled = len(result.assignments)
        total = filled + len(result.unfilled)
        group_a = sum(1 for a in result.assignments if a.shift.site_group == SiteGroup.A)
        group_b = filled - group_a
        counts: dict[str, int] = defaultdict(int)
        night_dates: dict[str, set] = defaultdict(set)
        for a in result.assignments:
            counts[a.physician_id] += 1
            if a.shift.time == "2400h":
                night_dates[a.physician_id].add(a.date)
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

    # ------------------------------------------------------------------
    # Delegate repair_pass and assign_on_calls to ScheduleGenerator
    # (they operate only on the ScheduleResult + state restored from it,
    #  so they work identically regardless of how the result was produced)
    # ------------------------------------------------------------------

    def repair_pass(self, result: ScheduleResult, n_attempts: int = 50) -> ScheduleResult:
        """Delegate post-solve repair to ScheduleGenerator."""
        from scheduler.backend.generator import ScheduleGenerator
        gen = ScheduleGenerator(list(self.submissions.values()), self.roster, self.config)
        # Restore state so repair_pass sees the CP-SAT assignments
        gen._restore_state_from_result(result)
        return gen.repair_pass(result, n_attempts)

    def assign_on_calls(self, result: ScheduleResult) -> ScheduleResult:
        """Delegate on-call assignment to ScheduleGenerator."""
        from scheduler.backend.generator import ScheduleGenerator
        gen = ScheduleGenerator(list(self.submissions.values()), self.roster, self.config)
        return gen.assign_on_calls(result)
