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


class NotSubmittedRow(BaseModel):
    physician_id: str
    physician_name: str
    status: str  # "not_started" | "draft"
    email: str = ""


class ImportDirectoryResponse(BaseModel):
    year: int
    month: int
    directory: str
    physicians: list[PhysicianImportResult]
    total_physicians: int
    valid_physicians: int
    # Only populated by /api/sked/import -- active roster physicians who
    # haven't submitted yet (no response at all, or still "draft"), for the
    # "highlight + send reminder" UI. Empty for the directory/flat-file
    # import routes, which have no way to know who hasn't submitted.
    not_submitted: list[NotSubmittedRow] = []


class OverrideRequest(BaseModel):
    physician_id: str
    rule: str


class OverrideAllRequest(BaseModel):
    physician_id: str


class ValidationSummaryItem(BaseModel):
    physician_id: str
    physician_name: str
    errors: list[str]


class ValidationSummaryResponse(BaseModel):
    items: list[ValidationSummaryItem]


# ---------------------------------------------------------------------------
# Outgoing email — reminder notifications
# ---------------------------------------------------------------------------

class EmailStatusResponse(BaseModel):
    configured: bool


class SendReminderEmailRequest(BaseModel):
    physician_id: str


class SendReminderEmailResponse(BaseModel):
    ok: bool
    status: str = ""


# ---------------------------------------------------------------------------
# Monthly shift requests — roster sync + magic links (sked) + email
# ---------------------------------------------------------------------------

class SkedStatusResponse(BaseModel):
    configured: bool


class PeriodInfo(BaseModel):
    id: str
    label: str
    opens_at: str
    closes_at: str


class PeriodsResponse(BaseModel):
    periods: list[PeriodInfo]


class PhysicianLinkRequest(BaseModel):
    physician_id: str
    period_id: str


class PhysicianLinkResponse(BaseModel):
    url: str


class SendMonthlyRequestsRequest(BaseModel):
    period_id: str
    label: str
    opens_at: str  # ISO 8601
    closes_at: str  # ISO 8601
    # Local filesystem path to that month's master schedule .xlsx. Optional
    # when re-sending/targeting an already-existing period on sked (it
    # already has a template) -- omit to reuse whatever's already uploaded
    # there instead of re-uploading.
    template_path: str | None = None
    extra_message: str = ""  # free text from the scheduler, included in the email body
    physician_ids: list[str] | None = None  # None = every active physician; otherwise just these


class SendMonthlyRequestsResult(BaseModel):
    physician_id: str
    physician_name: str
    email: str = ""
    status: str  # "sent" | "no_email" | "send_failed"
    detail: str = ""


class SendMonthlyRequestsResponse(BaseModel):
    ok: bool
    sent_count: int
    results: list[SendMonthlyRequestsResult]
    needs_attention: list[SendMonthlyRequestsResult]


# ---------------------------------------------------------------------------
# Annual preference survey — completion tracking (viewer only; encoding
# free-text requests into solver rules is done by hand, not by this app)
# ---------------------------------------------------------------------------

class SurveyInfo(BaseModel):
    id: str
    label: str
    opens_at: str
    closes_at: str


class SurveysResponse(BaseModel):
    surveys: list[SurveyInfo]


class SurveyCompletionRow(BaseModel):
    physician_id: str
    physician_name: str
    active: bool
    status: str  # "not_started" | "draft" | "submitted"
    updated_at: str | None = None
    data: dict | None = None  # full response payload, present when status != "not_started"


class SurveyCompletionResponse(BaseModel):
    survey: SurveyInfo
    rows: list[SurveyCompletionRow]
    submitted_count: int
    total_active: int


class ResendSurveyLinkRequest(BaseModel):
    survey_id: str
    physician_id: str


class ResendSurveyLinkResponse(BaseModel):
    ok: bool
    status: str  # "sent"
    detail: str = ""


class SkedImportRequest(BaseModel):
    period_id: str
    year: int
    month: int


class ResendMonthlyRequestRequest(BaseModel):
    period_id: str
    physician_id: str


class ResendMonthlyRequestResponse(BaseModel):
    ok: bool
    status: str  # "sent"
    detail: str = ""


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


class PhysicianRequestedSchema(BaseModel):
    shifts_requested: int
    shifts_max: int
    shifts_2400h_requested: int
    shifts_0600h_requested: int


class ScheduleResponse(BaseModel):
    year: int
    month: int
    assignments: list[AssignmentSchema]
    unfilled: list[UnfilledSlotSchema]
    issues: list[str]
    stats: Optional[ScheduleStatsSchema]
    on_calls: list[OnCallAssignmentSchema] = []
    # Keyed by physician_id -- what each physician actually asked for that
    # month (from their submission), for the Individual Schedules viewer to
    # show alongside what they were actually scheduled. Empty if submissions
    # aren't in memory (e.g. schedule loaded from a saved file post-restart).
    requested: dict[str, PhysicianRequestedSchema] = {}


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
# Physician roster editor — full PhysicianConfig fields, read + write.
# ---------------------------------------------------------------------------

class PhysicianDetail(BaseModel):
    id: str
    name: str
    email: str = ""
    notes: str = ""
    active: bool = True
    last_name: str = ""
    first_name: str = ""
    aliases: list[str] = []

    max_consecutive_shifts: int = 3
    max_consecutive_nights: int = 3
    group_b_site_preference: Optional[str] = None
    forbidden_sites: list[str] = []
    only_2400h: bool = False
    only_0600h: bool = False
    post_block_rest_days: int = 0
    post_block_min_length: int = 2
    call_linkage: Optional[str] = None
    max_consecutive_same_site: Optional[int] = None
    avoid_weekday: Optional[str] = None
    prefer_weekend_clumping: bool = False
    prefer_weekends: bool = False
    max_weekends: Optional[int] = None
    honor_all_requests: bool = False
    prefer_singleton_nights: bool = False
    forbidden_shift_times: list[str] = []
    no_call: bool = False
    avoid_mondays: bool = False
    rest_after_late_shift: bool = False
    max_consecutive_1800h: int = 3
    cap_at_requested: bool = False
    special_provisions: bool = False
    casual: bool = False
    rule_overrides: dict[str, Optional[int]] = {}


class PhysicianDetailsResponse(BaseModel):
    physicians: list[PhysicianDetail]


class PhysicianUpdateRequest(PhysicianDetail):
    """Same shape as PhysicianDetail — the full, edited record to save."""
    pass


class CreatePhysicianRequest(BaseModel):
    id: str            # single-word roster id, e.g. "Dickey"
    first_name: str
    last_name: str


class RemovePhysicianRequest(BaseModel):
    confirmation: str    # must exactly equal the literal word "REMOVE"


class RemovePhysicianResponse(BaseModel):
    ok: bool
    status: str = ""


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
