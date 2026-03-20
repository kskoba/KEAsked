"""
Pydantic schemas for the FastAPI server.

These mirror the backend dataclasses but are serialisable to/from JSON
so the Electron renderer can consume them directly.
"""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

class ShiftSchema(BaseModel):
    time: str       # "0600h", "2400h", etc.
    site: str       # "RAH A side", "NEHC", etc.
    code: str       # "{time} {site}"
    site_group: str # "A" or "B"


class ViolationSchema(BaseModel):
    rule: str
    description: str


# ---------------------------------------------------------------------------
# Import / validation
# ---------------------------------------------------------------------------

class ValidationIssueSchema(BaseModel):
    severity: str   # "error" | "warning"
    rule: str
    message: str
    physician_id: str


class PhysicianImportResult(BaseModel):
    physician_id: str
    physician_name: str
    shifts_requested: int
    shifts_min: int
    shifts_max: int
    shifts_2400h_requested: int
    shifts_0600h_requested: int
    valid_days: int
    valid_blocks: int
    valid_weekend_days: int
    anchored_days: int
    issues: list[ValidationIssueSchema]
    is_valid: bool


class ImportDirectoryResponse(BaseModel):
    year: int
    month: int
    directory: str
    physicians: list[PhysicianImportResult]
    total_physicians: int
    valid_physicians: int


# ---------------------------------------------------------------------------
# Schedule generation
# ---------------------------------------------------------------------------

class AssignmentSchema(BaseModel):
    date: str           # "YYYY-MM-DD"
    shift: ShiftSchema
    physician_id: str
    physician_name: str
    is_manual: bool
    is_claude: bool = False


class CandidateSchema(BaseModel):
    physician_id: str
    physician_name: str
    violations: list[ViolationSchema]


class UnfilledSlotSchema(BaseModel):
    date: str
    shift: ShiftSchema
    candidates: list[CandidateSchema]


class ScheduleStatsSchema(BaseModel):
    total_slots: int
    filled_slots: int
    unfilled_slots: int
    group_a_count: int
    group_b_count: int
    group_a_pct: float
    group_b_pct: float
    physician_counts: dict[str, int]
    physician_singletons: dict[str, int]


class OnCallAssignmentSchema(BaseModel):
    date: str           # "YYYY-MM-DD"
    call_type: str      # "DOC" or "NOC"
    physician_id: str
    physician_name: str


class ScheduleResponse(BaseModel):
    year: int
    month: int
    assignments: list[AssignmentSchema]
    unfilled: list[UnfilledSlotSchema]
    issues: list[str]
    stats: Optional[ScheduleStatsSchema]
    on_calls: list[OnCallAssignmentSchema] = []


# ---------------------------------------------------------------------------
# Manual assignment
# ---------------------------------------------------------------------------

class ManualAssignRequest(BaseModel):
    date: str           # "YYYY-MM-DD"
    shift_code: str     # e.g. "0600h RAH A side"
    physician_id: str


class ManualAssignResponse(BaseModel):
    success: bool
    violations: list[ViolationSchema]   # rules broken (if any)
    message: str


# ---------------------------------------------------------------------------
# Physician list
# ---------------------------------------------------------------------------

class PhysicianInfo(BaseModel):
    id: str
    name: str
    active: bool
    max_consecutive_shifts: int
    group_b_site_preference: Optional[str]
    forbidden_sites: list[str]


class PhysiciansResponse(BaseModel):
    physicians: list[PhysicianInfo]


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    directory: str
    year: int
    month: int
    use_claude: bool = False    # if True, call Claude API for unfilled slots


class ImportRequest(BaseModel):
    directory: str
    year: int
    month: int


class ImportFlatRequest(BaseModel):
    file: str
    year: int
    month: int


class GenerateCachedRequest(BaseModel):
    year: int
    month: int
    use_claude: bool = False


class DetectFlatResponse(BaseModel):
    year: int
    month: int


class AdjustRequest(BaseModel):
    instruction: str    # free-text, e.g. "give Wittmeier 2 less shifts"
    year: int
    month: int


class AdjustResponse(BaseModel):
    schedule: ScheduleResponse
    applied: list[str]      # human-readable description of each applied change
    rejected: list[str]     # changes Claude suggested that failed validation
