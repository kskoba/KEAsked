"""
Unit tests for the submission validator.

These tests use synthetic PhysicianSubmission objects so they run without
any Excel files.
"""

from __future__ import annotations

import datetime
import math
import pytest

from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.validator import validate


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _make_day(
    date: datetime.date,
    wants_to_work: bool = True,
    block_count: int = 2,
    anchor: bool = False,
) -> DayAvailability:
    """Build a DayAvailability with the given block configuration."""
    if not wants_to_work:
        return DayAvailability(date=date, wants_to_work=False)

    blocks: set[int] = set()
    if anchor:
        blocks.add(0)  # 0600h anchor block
    # Fill remaining slots with non-anchor blocks
    non_anchor = [1, 2, 3]
    for b in non_anchor:
        if len(blocks) >= block_count:
            break
        blocks.add(b)

    return DayAvailability(
        date=date,
        wants_to_work=True,
        available_blocks=frozenset(blocks),
    )


def _make_submission(
    n: int,
    valid_days: int,
    total_blocks: int,
    valid_weekend_days: int,
    anchored_days: int,
    year: int = 2026,
    month: int = 4,
    rule_overrides: dict | None = None,
) -> PhysicianSubmission:
    """
    Build a PhysicianSubmission that exactly matches the requested counts.

    Strategy:
    - Place anchored_days first (anchor block = block 0).
    - Then fill valid_weekend_days (non-anchored weekend days).
    - Then fill remaining valid_days with plain weekday days.
    - Blocks per day are raised evenly so total_blocks is met.
    """
    import calendar

    days_in_month = calendar.monthrange(year, month)[1]
    all_dates = [datetime.date(year, month, d) for d in range(1, days_in_month + 1)]

    # Categorise dates
    weekends = [d for d in all_dates if d.weekday() in (4, 5, 6)]
    weekdays = [d for d in all_dates if d.weekday() not in (4, 5, 6)]

    # Build the set of "valid" dates
    selected: list[tuple[datetime.date, bool]] = []  # (date, is_anchor)

    # Anchors: prefer weekdays first (to not burn weekend quota unnecessarily)
    anchor_dates = (weekdays + weekends)[:anchored_days]
    for d in anchor_dates:
        selected.append((d, True))

    remaining_weekends = [d for d in weekends if d not in anchor_dates]
    remaining_weekdays = [d for d in weekdays if d not in anchor_dates]

    # Non-anchored weekend valid days
    non_anchor_weekends_needed = max(0, valid_weekend_days - sum(
        1 for d, _ in selected if d.weekday() in (4, 5, 6)
    ))
    for d in remaining_weekends[:non_anchor_weekends_needed]:
        selected.append((d, False))

    # Fill remaining valid days with weekdays
    filled_so_far = len(selected)
    for d in remaining_weekdays[: max(0, valid_days - filled_so_far)]:
        selected.append((d, False))

    # Determine blocks per day to meet total_blocks.
    # Minimum 2 per day (rule requirement); distribute extras round-robin.
    num_valid = len(selected)
    if num_valid > 0:
        base_blocks = max(2, total_blocks // num_valid)
        extra = max(0, total_blocks - base_blocks * num_valid)
    else:
        base_blocks = 2
        extra = 0

    # Build block-count list per selected day
    block_counts = [base_blocks + (1 if i < extra else 0) for i in range(num_valid)]
    # Cap at 5 (there are only 5 blocks)
    block_counts = [min(5, b) for b in block_counts]

    selected_map: dict[datetime.date, tuple[bool, int]] = {
        d: (is_anchor, block_counts[i]) for i, (d, is_anchor) in enumerate(selected)
    }

    days: list[DayAvailability] = []
    for d in all_dates:
        if d in selected_map:
            is_anchor, bc = selected_map[d]
            days.append(_make_day(d, wants_to_work=True, block_count=bc, anchor=is_anchor))
        else:
            days.append(DayAvailability(date=d, wants_to_work=False))

    return PhysicianSubmission(
        physician_id="DR001",
        physician_name="Test Physician",
        year=year,
        month=month,
        shifts_requested=n,
        days=days,
        rule_overrides=rule_overrides or {},
    )


# --------------------------------------------------------------------------- #
# Tests — happy path
# --------------------------------------------------------------------------- #

class TestValidSubmission:
    def test_exactly_meets_all_minimums(self):
        n = 10
        sub = _make_submission(
            n=n,
            valid_days=math.ceil(n * 1.5),     # 15
            total_blocks=n * 4,                 # 40
            valid_weekend_days=math.ceil(n * 0.6),  # 6
            anchored_days=math.ceil(n / 2),     # 5
        )
        result = validate(sub)
        assert result.is_valid, [i.message for i in result.errors]

    def test_exceeds_all_minimums(self):
        n = 8
        sub = _make_submission(
            n=n,
            valid_days=20,
            total_blocks=50,
            valid_weekend_days=8,
            anchored_days=8,
        )
        result = validate(sub)
        assert result.is_valid


# --------------------------------------------------------------------------- #
# Tests — individual rule failures
# --------------------------------------------------------------------------- #

class TestMinValidDays:
    def test_too_few_valid_days_is_error(self):
        n = 10
        sub = _make_submission(
            n=n,
            valid_days=math.ceil(n * 1.5) - 1,  # 14, need 15
            total_blocks=n * 4,
            valid_weekend_days=math.ceil(n * 0.6),
            anchored_days=math.ceil(n / 2),
        )
        result = validate(sub)
        assert not result.is_valid
        rule_ids = {i.rule for i in result.errors}
        assert "min_valid_days" in rule_ids

    def test_override_lowers_threshold(self):
        n = 10
        # Would normally fail (14 < 15 required) but override sets minimum to 14
        sub = _make_submission(
            n=n,
            valid_days=14,
            total_blocks=n * 4,
            valid_weekend_days=math.ceil(n * 0.6),
            anchored_days=math.ceil(n / 2),
            rule_overrides={"min_valid_days": 14},
        )
        result = validate(sub)
        rule_ids = {i.rule for i in result.errors}
        assert "min_valid_days" not in rule_ids

    def test_override_none_disables_rule(self):
        n = 10
        sub = _make_submission(
            n=n,
            valid_days=0,   # would always fail normally
            total_blocks=n * 4,
            valid_weekend_days=math.ceil(n * 0.6),
            anchored_days=math.ceil(n / 2),
            rule_overrides={"min_valid_days": None},
        )
        result = validate(sub)
        rule_ids = {i.rule for i in result.errors}
        assert "min_valid_days" not in rule_ids


class TestMinValidBlocks:
    def test_too_few_blocks_is_error(self):
        n = 6
        sub = _make_submission(
            n=n,
            valid_days=math.ceil(n * 1.5),
            total_blocks=(n * 4) - 1,  # one short
            valid_weekend_days=math.ceil(n * 0.6),
            anchored_days=math.ceil(n / 2),
        )
        result = validate(sub)
        rule_ids = {i.rule for i in result.errors}
        assert "min_valid_blocks" in rule_ids


class TestMinWeekendDays:
    def test_too_few_weekend_days_is_error(self):
        n = 10
        sub = _make_submission(
            n=n,
            valid_days=math.ceil(n * 1.5),
            total_blocks=n * 4,
            valid_weekend_days=math.ceil(n * 0.6) - 1,  # one short
            anchored_days=math.ceil(n / 2),
        )
        result = validate(sub)
        rule_ids = {i.rule for i in result.errors}
        assert "min_weekend_days" in rule_ids


class TestMinAnchoredDays:
    def test_too_few_anchored_days_is_error(self):
        n = 10
        sub = _make_submission(
            n=n,
            valid_days=math.ceil(n * 1.5),
            total_blocks=n * 4,
            valid_weekend_days=math.ceil(n * 0.6),
            anchored_days=math.ceil(n / 2) - 1,  # one short
        )
        result = validate(sub)
        rule_ids = {i.rule for i in result.errors}
        assert "min_anchored_days" in rule_ids


# --------------------------------------------------------------------------- #
# Tests — warnings
# --------------------------------------------------------------------------- #

class TestWarnings:
    def test_z_row_with_partial_block_produces_warning(self):
        date = datetime.date(2026, 4, 1)
        # wants_to_work=True but only 1 block → not a valid day → should warn
        day = DayAvailability(
            date=date,
            wants_to_work=True,
            available_blocks=frozenset({1}),  # only 1 block
        )
        n = 1
        sub = PhysicianSubmission(
            physician_id="DR002",
            physician_name="Partial Block Physician",
            year=2026,
            month=4,
            shifts_requested=n,
            days=[day] + [
                _make_day(
                    datetime.date(2026, 4, d),
                    wants_to_work=True,
                    block_count=2,
                    anchor=(d <= math.ceil(n / 2)),
                )
                for d in range(2, 30)
            ],
        )
        result = validate(sub)
        warning_rules = {i.rule for i in result.warnings}
        assert "z_row_partial_blocks" in warning_rules

    def test_zero_shifts_requested_produces_warning(self):
        sub = _make_submission(
            n=0,
            valid_days=0,
            total_blocks=0,
            valid_weekend_days=0,
            anchored_days=0,
        )
        result = validate(sub)
        warning_rules = {i.rule for i in result.warnings}
        assert "zero_shifts_requested" in warning_rules
