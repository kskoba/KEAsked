"""
Shift request submission validator.

Implements all rules from request-submission-rules.md.

Rules are stateless functions that operate on a PhysicianSubmission and
return a list of ValidationIssues.  The top-level `validate()` function
runs all rules and applies any per-physician overrides.
"""

from __future__ import annotations

import math
from typing import Callable

from scheduler.backend.models import (
    DayAvailability,
    PhysicianSubmission,
    ValidationIssue,
    ValidationResult,
)


# --------------------------------------------------------------------------- #
# Helper
# --------------------------------------------------------------------------- #

def _error(rule: str, message: str, physician_id: str) -> ValidationIssue:
    return ValidationIssue(
        severity="error",
        rule=rule,
        message=message,
        physician_id=physician_id,
    )


def _warning(rule: str, message: str, physician_id: str) -> ValidationIssue:
    return ValidationIssue(
        severity="warning",
        rule=rule,
        message=message,
        physician_id=physician_id,
    )


# --------------------------------------------------------------------------- #
# Individual rules
# --------------------------------------------------------------------------- #

def check_min_valid_days(
    sub: PhysicianSubmission,
    override: int | None = None,
) -> list[ValidationIssue]:
    """Rule 1: at least ceil(n * 1.5) valid days."""
    n = sub.shifts_requested
    required = override if override is not None else math.ceil(n * 1.5)
    actual = sum(1 for d in sub.days if d.is_valid_day)
    if actual < required:
        return [
            _error(
                "min_valid_days",
                f"Only {actual} valid day(s) available; need at least {required} "
                f"(ceil({n} × 1.5)) for {n} requested shift(s).",
                sub.physician_id,
            )
        ]
    return []


def check_min_valid_blocks(
    sub: PhysicianSubmission,
    override: int | None = None,
) -> list[ValidationIssue]:
    """Rule 2: at least n * 4 valid blocks in total."""
    n = sub.shifts_requested
    required = override if override is not None else n * 4
    actual = sum(d.valid_block_count for d in sub.days if d.is_valid_day)
    if actual < required:
        return [
            _error(
                "min_valid_blocks",
                f"Only {actual} valid block(s) available across all days; "
                f"need at least {required} ({n} × 4) for {n} requested shift(s).",
                sub.physician_id,
            )
        ]
    return []


def check_min_weekend_days(
    sub: PhysicianSubmission,
    override: int | None = None,
) -> list[ValidationIssue]:
    """Rule 3: at least ceil(n * 0.6) valid weekend days."""
    n = sub.shifts_requested
    required = override if override is not None else math.ceil(n * 0.6)
    actual = sum(1 for d in sub.days if d.is_valid_weekend)
    if actual < required:
        return [
            _error(
                "min_weekend_days",
                f"Only {actual} valid weekend day(s) available; need at least "
                f"{required} (ceil({n} × 0.6)) for {n} requested shift(s).",
                sub.physician_id,
            )
        ]
    return []


def check_min_anchored_days(
    sub: PhysicianSubmission,
    override: int | None = None,
) -> list[ValidationIssue]:
    """Rule 4: at least ceil(n / 2) anchored days."""
    n = sub.shifts_requested
    required = override if override is not None else math.ceil(n / 2)
    actual = sum(1 for d in sub.days if d.is_anchored)
    if actual < required:
        return [
            _error(
                "min_anchored_days",
                f"Only {actual} anchored day(s) available; need at least "
                f"{required} (ceil({n} / 2)) for {n} requested shift(s).",
                sub.physician_id,
            )
        ]
    return []


def check_z_row_partial_blocks(sub: PhysicianSubmission) -> list[ValidationIssue]:
    """
    Warning: a day marked Z but with fewer than 2 full blocks is flagged.
    These days do not count as valid days, so they may surprise the physician.
    """
    issues = []
    for d in sub.days:
        if d.wants_to_work and not d.is_valid_day:
            issues.append(
                _warning(
                    "z_row_partial_blocks",
                    f"{d.date}: marked as desired (Z) but only {d.valid_block_count} "
                    f"complete block(s) available — does not count as a valid day.",
                    sub.physician_id,
                )
            )
    return issues


def check_zero_shifts_requested(sub: PhysicianSubmission) -> list[ValidationIssue]:
    """Sanity check: requesting 0 shifts is almost certainly a mistake."""
    if sub.shifts_requested == 0:
        return [
            _warning(
                "zero_shifts_requested",
                "Physician requested 0 shifts this month.",
                sub.physician_id,
            )
        ]
    return []


# --------------------------------------------------------------------------- #
# Rule registry — ordered list of (rule_id, function) pairs.
# Override-aware rules accept an optional int parameter.
# --------------------------------------------------------------------------- #

_OVERRIDE_RULES: dict[
    str,
    Callable[[PhysicianSubmission, int | None], list[ValidationIssue]],
] = {
    "min_valid_days": check_min_valid_days,
    "min_valid_blocks": check_min_valid_blocks,
    "min_weekend_days": check_min_weekend_days,
    "min_anchored_days": check_min_anchored_days,
}

_FIXED_RULES: list[Callable[[PhysicianSubmission], list[ValidationIssue]]] = [
    check_z_row_partial_blocks,
    check_zero_shifts_requested,
]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def validate(sub: PhysicianSubmission) -> ValidationResult:
    """
    Run all validation rules against a PhysicianSubmission.

    Per-physician overrides are respected:
    - If rule_overrides[rule_id] is an int, that value replaces the computed
      minimum for that rule.
    - If rule_overrides[rule_id] is None, the rule is skipped entirely.
    """
    issues: list[ValidationIssue] = []

    for rule_id, fn in _OVERRIDE_RULES.items():
        if rule_id in sub.rule_overrides:
            override_value = sub.rule_overrides[rule_id]
            if override_value is None:
                continue  # Rule disabled for this physician
            issues.extend(fn(sub, int(override_value)))
        else:
            issues.extend(fn(sub))

    for fn in _FIXED_RULES:
        issues.extend(fn(sub))

    return ValidationResult(
        physician_id=sub.physician_id,
        physician_name=sub.physician_name,
        issues=issues,
    )


def validate_all(
    submissions: list[PhysicianSubmission],
) -> list[ValidationResult]:
    """Validate a list of submissions and return one result per physician."""
    return [validate(sub) for sub in submissions]
