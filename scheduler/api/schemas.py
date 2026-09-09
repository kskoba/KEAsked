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
    is_hard: bool = False


# ---------------------------------------------------------------------------
# Import / validation
# ---------------------------------------------------------------------------

class ValidationIssueSchema(BaseModel):
    severity: str   # "error" | "warning"
    rule: str
    message: str
    physician_id: str
    overridden: bool = False


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


class OverrideRequest(BaseModel):
    physician_id: str
    rule: str


class OverrideAllRequest(BaseModel):
    physician_id: str


class ValidationSummaryItem(BaseModel):
    physician_name: str
    errors: list[str]


class ValidationSummaryResponse(BaseModel):
    items: list[ValidationSummaryItem]


class OverrideLogItem(BaseModel):
    physician_name: str
    rule: str
    message: str


class OverrideLogResponse(BaseModel):
    items: list[OverrideLogItem]


# ---------------------------------------------------------------------------
# ByteBloc integration
# ---------------------------------------------------------------------------
# See scheduler/backend/bytebloc.py for the hard safety rule: nothing is
# ever sent to ByteBloc without a human typing the confirmation phrase.

class ByteBlocRequestPreviewItem(BaseModel):
    physician_id: str
    physician_name: str
    day: str            # yyyy-MM-dd
    shift_code: str      # KEAsked's internal code, e.g. "0600h RAH A side"


class ByteBlocPreviewResponse(BaseModel):
    configured: bool                          # False if bytebloc.yaml doesn't exist yet
    group_code: str = ""
    location_code: str = ""
    requester_id: str = ""
    sked_start_date: str = ""                 # yyyy-MM-dd, for display
    items: list[ByteBlocRequestPreviewItem] = []
    warnings: list[str] = []
    physician_count: int = 0
    request_count: int = 0


class ByteBlocSendRequest(BaseModel):
    confirmation: str    # must exactly equal bytebloc.CONFIRMATION_PHRASE


class ByteBlocSendResponse(BaseModel):
    ok: bool
    status: str = ""
    raw: dict | None = None


# ---------------------------------------------------------------------------
# Schedule generation
# ---------------------------------------------------------------------------

class AssignmentSchema(BaseModel):
    date: str           # "YYYY-MM-DD"
    shift: ShiftSchema
    physician_id: str
    physician_name: str
    is_manual: bool


class CandidateSchema(BaseModel):
    physician_id: str
    physician_name: str
    violations: list[ViolationSchema]
    is_hard_blocked: bool = False   # True when a hard safety/competency rule is violated


class CandidatesResponse(BaseModel):
    date: str
    shift_code: str
    candidates: list[CandidateSchema]


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
    solver_status: str | None = None
    optimality_gap_pct: float | None = None


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
    time_limit_seconds: int | None = None  # None → use server default (600s)


class DetectFlatResponse(BaseModel):
    year: int
    month: int


class LoadScheduleRequest(BaseModel):
    file: str               # absolute path to a previously exported schedule .xlsx


class OnCallCandidateSchema(BaseModel):
    physician_id: str
    physician_name: str
    violations: list[str] = []     # human-readable constraint violations (shown as warnings)


class OnCallCandidatesResponse(BaseModel):
    date: str
    call_type: str                 # "DOC" or "NOC"
    current_physician_id: str | None = None
    candidates: list[OnCallCandidateSchema]


class AssignOnCallRequest(BaseModel):
    date: str       # YYYY-MM-DD
    call_type: str  # "DOC" or "NOC"
    physician_id: str   # empty string = remove the on-call assignment
