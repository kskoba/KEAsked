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
    AdjustRequest,
    AdjustResponse,
    AssignmentSchema,
    CandidateSchema,
    DetectFlatResponse,
    GenerateCachedRequest,
    GenerateRequest,
    ImportFlatRequest,
    ImportRequest,
    ManualAssignRequest,
    ManualAssignResponse,
    PhysicianImportResult,
    OnCallAssignmentSchema,
    PhysicianInfo,
    PhysiciansResponse,
    ImportDirectoryResponse,
    ScheduleResponse,
    ScheduleStatsSchema,
    ShiftSchema,
    UnfilledSlotSchema,
    ValidationIssueSchema,
    ViolationSchema,
)
from scheduler.backend.config import load_roster
from scheduler.backend.generator import (
    Assignment,
    OnCallAssignment,
    ScheduleGenerator,
    ScheduleResult,
    generate_schedule,
)
from scheduler.backend.importer import import_directory, import_single_file
from scheduler.backend.importer_flat import import_flat_file
from scheduler.backend.models import PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES, BLOCKS, SHIFT_TO_BLOCK, Shift
from scheduler.backend.validator import validate

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
}


def _apply_roster(submissions: list[PhysicianSubmission], roster: dict) -> None:
    """
    Merge per-physician config (rule overrides etc.) into each submission.
    Matching is case-insensitive so flat-file names like "BRAUN" match
    roster IDs like "Braun".
    """
    lower_roster = {k.lower(): v for k, v in roster.items()}
    for sub in submissions:
        cfg = roster.get(sub.physician_id) or lower_roster.get(sub.physician_id.lower())
        if cfg:
            sub.rule_overrides = dict(cfg.rule_overrides)


def _build_import_results(
    submissions: list[PhysicianSubmission],
) -> list[PhysicianImportResult]:
    results = []
    for sub in submissions:
        vr = validate(sub)
        valid_days = sum(1 for d in sub.days if d.is_valid_day)
        valid_blocks = sum(len(d.available_blocks) for d in sub.days if d.is_valid_day)
        valid_weekends = sum(1 for d in sub.days if d.is_valid_weekend)
        anchored = sum(1 for d in sub.days if d.is_anchored)
        results.append(PhysicianImportResult(
            physician_id=sub.physician_id,
            physician_name=sub.physician_name,
            shifts_requested=sub.shifts_requested,
            shifts_min=sub.shifts_min,
            shifts_max=sub.shifts_max,
            shifts_2400h_requested=sub.shifts_2400h_requested,
            shifts_0600h_requested=sub.shifts_0600h_requested,
            valid_days=valid_days,
            valid_blocks=valid_blocks,
            valid_weekend_days=valid_weekends,
            anchored_days=anchored,
            issues=[
                ValidationIssueSchema(
                    severity=i.severity,
                    rule=i.rule,
                    message=i.message,
                    physician_id=i.physician_id or sub.physician_id,
                )
                for i in vr.issues
            ],
            is_valid=vr.is_valid,
        ))
    return results


def _load_scheduler_config() -> dict:
    import os
    with _SCHEDULER_CONFIG_PATH.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    # Inject Anthropic API key from config into environment if not already set.
    api_key = cfg.get("anthropic_api_key", "")
    if api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = api_key
    return cfg


def _shift_to_schema(shift: Shift) -> ShiftSchema:
    return ShiftSchema(
        time=shift.time,
        site=shift.site,
        code=shift.code,
        site_group=shift.site_group.value,
    )


def _result_to_response(result: ScheduleResult) -> ScheduleResponse:
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
                is_claude=a.is_claude,
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
                            ViolationSchema(rule=v.rule, description=v.description)
                            for v in c.violations
                        ],
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
    )


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

    _apply_roster(submissions, roster)
    results = _build_import_results(submissions)
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

    _apply_roster(submissions, roster)
    results = _build_import_results(submissions)
    _state.update(submissions=submissions, roster=roster, scheduler_config=scheduler_cfg,
                  year=body.year, month=body.month, directory=None, source_file=str(file_path))

    valid_count = sum(1 for r in results if r.is_valid)
    return ImportDirectoryResponse(year=body.year, month=body.month,
                                   directory=str(file_path),
                                   physicians=results, total_physicians=len(results),
                                   valid_physicians=valid_count)


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
    n_iterations = 200

    _state["progress"] = {"current": 0, "total": n_iterations, "running": True, "best_unfilled": None}

    def progress_cb(current: int, total: int, best_score: float) -> None:
        _state["progress"]["current"] = current
        # Derive approximate unfilled count from score: score = -unfilled*1000 + ...
        # Just show the raw best score for now
        _state["progress"]["best_unfilled"] = round(-best_score / 1000)

    try:
        gen = ScheduleGenerator(submissions, roster, cfg)
        result = await asyncio.to_thread(
            gen.run_best_of, n_iterations, body.year, body.month, progress_cb
        )
        # Post-solve repair: juggle adjacent assignments to fill remaining gaps
        if result.unfilled:
            result = await asyncio.to_thread(gen.repair_pass, result, 50)
        # Claude-assisted global improvement (runs before on-calls so it can
        # reshape the regular schedule; only active when use_claude=True).
        if body.use_claude:
            await asyncio.to_thread(
                _claude_improve_schedule, result, gen, submissions, roster
            )
        # Sync issues list: only keep entries for slots that remain unfilled
        # (repair pass and Claude may have filled slots that still appear in issues)
        unfilled_keys = {
            f"{u.date.strftime('%b %d')} {u.shift.code}" for u in result.unfilled
        }
        result.issues = [i for i in result.issues if any(k in i for k in unfilled_keys)] \
            if result.unfilled else []
        # Assign on-call shifts after the regular schedule is complete
        result = await asyncio.to_thread(gen.assign_on_calls, result)
    except Exception as exc:
        _state["progress"]["running"] = False
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}\n{traceback.format_exc()}")

    _state["progress"] = {"current": n_iterations, "total": n_iterations, "running": False, "best_unfilled": len(result.unfilled)}
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


@app.post("/api/adjust", response_model=AdjustResponse)
async def adjust_schedule(body: AdjustRequest) -> AdjustResponse:
    """
    Apply a free-text scheduling instruction via Claude.
    Example: "give Wittmeier 2 fewer shifts and give Lam-Rico 2 more shifts"
    Returns the updated schedule plus lists of applied/rejected operations.
    """
    result: ScheduleResult | None = _state.get("result")
    gen: ScheduleGenerator | None = _state.get("generator")
    if result is None or gen is None:
        raise HTTPException(status_code=404, detail="No schedule generated yet.")

    client = _get_anthropic_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Claude API unavailable.")

    # Build compact schedule summary per physician
    per_phys: dict[str, list] = {}
    for a in result.assignments:
        per_phys.setdefault(a.physician_name, []).append(
            f"{a.date} {a.shift.code}"
        )
    summary_lines = [
        f"  {name} ({len(shifts)} shifts): {', '.join(sorted(shifts))}"
        for name, shifts in sorted(per_phys.items())
    ]

    prompt = (
        f"You are adjusting an emergency physician schedule.\n\n"
        f"INSTRUCTION: {body.instruction}\n\n"
        f"CURRENT SCHEDULE:\n" + "\n".join(summary_lines) + "\n\n"
        f"RULES:\n"
        f"- 22h minimum gap between consecutive shifts for the same physician\n"
        f"- No consecutive Group A (RAH A side / RAH B side) assignments\n"
        f"- Respect physician availability\n\n"
        f"Output one operation per line. Use exactly these formats:\n"
        f"REMOVE: physician_name | YYYY-MM-DD | shift_code\n"
        f"ADD: physician_name | YYYY-MM-DD | shift_code\n"
        f"MOVE: physician_name | YYYY-MM-DD | shift_code | YYYY-MM-DD | shift_code\n\n"
        f"Only suggest operations directly required by the instruction. "
        f"For remove operations prefer shifts on non-weekend days. "
        f"For add operations only use dates/shifts that appear in the schedule above.\n"
    )

    try:
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Claude API error: {exc}")

    applied: list[str] = []
    rejected: list[str] = []

    for line in message.content[0].text.splitlines():
        line = line.strip()
        uline = line.upper()
        if uline.startswith("REMOVE:"):
            parts = [p.strip() for p in line[7:].split("|")]
            if len(parts) != 3:
                rejected.append(f"Bad REMOVE format: {line}"); continue
            name, date_str, code = parts
            try:
                d = datetime.date.fromisoformat(date_str)
            except ValueError:
                rejected.append(f"Bad date in: {line}"); continue
            pid = next((p for p, s in gen.submissions.items()
                        if s.physician_name.lower() == name.lower()), None)
            if pid is None:
                rejected.append(f"Unknown physician: {name}"); continue
            existing = next((a for a in result.assignments
                             if a.physician_id == pid and a.date == d and a.shift.code == code), None)
            if existing is None:
                rejected.append(f"Assignment not found: {name} {date_str} {code}"); continue
            gen._unassign(pid, d, existing.shift)
            result.assignments = [a for a in result.assignments
                                   if not (a.physician_id == pid and a.date == d and a.shift.code == code)]
            applied.append(f"Removed {name} from {date_str} {code}")

        elif uline.startswith("ADD:"):
            parts = [p.strip() for p in line[4:].split("|")]
            if len(parts) != 3:
                rejected.append(f"Bad ADD format: {line}"); continue
            name, date_str, code = parts
            try:
                d = datetime.date.fromisoformat(date_str)
            except ValueError:
                rejected.append(f"Bad date in: {line}"); continue
            to_shift = next((s for block in BLOCKS for s in block if s.code == code), None)
            if to_shift is None:
                rejected.append(f"Unknown shift code: {code}"); continue
            pid = next((p for p, s in gen.submissions.items()
                        if s.physician_name.lower() == name.lower()), None)
            if pid is None:
                rejected.append(f"Unknown physician: {name}"); continue
            if any(a.date == d and a.shift.code == code for a in result.assignments):
                rejected.append(f"Slot already filled: {date_str} {code}"); continue
            violations = gen._check_constraints(pid, d, to_shift)
            if violations:
                descs = "; ".join(v.description for v in violations)
                rejected.append(f"Cannot add {name} to {date_str} {code}: {descs}"); continue
            gen._assign(pid, d, to_shift)
            result.assignments.append(Assignment(
                date=d, shift=to_shift, physician_id=pid,
                physician_name=gen.submissions[pid].physician_name, is_claude=True,
            ))
            result.unfilled = [u for u in result.unfilled
                                if not (u.date == d and u.shift.code == code)]
            applied.append(f"Added {name} to {date_str} {code}")

        elif uline.startswith("MOVE:"):
            parts = [p.strip() for p in line[5:].split("|")]
            if len(parts) != 5:
                rejected.append(f"Bad MOVE format: {line}"); continue
            name, from_date_str, from_code, to_date_str, to_code = parts
            try:
                from_d = datetime.date.fromisoformat(from_date_str)
                to_d = datetime.date.fromisoformat(to_date_str)
            except ValueError:
                rejected.append(f"Bad date in: {line}"); continue
            pid = next((p for p, s in gen.submissions.items()
                        if s.physician_name.lower() == name.lower()), None)
            if pid is None:
                rejected.append(f"Unknown physician: {name}"); continue
            existing = next((a for a in result.assignments
                             if a.physician_id == pid and a.date == from_d and a.shift.code == from_code), None)
            if existing is None:
                rejected.append(f"Assignment not found: {name} {from_date_str} {from_code}"); continue
            to_shift = next((s for block in BLOCKS for s in block if s.code == to_code), None)
            if to_shift is None:
                rejected.append(f"Unknown shift code: {to_code}"); continue
            if any(a.date == to_d and a.shift.code == to_code for a in result.assignments):
                rejected.append(f"Destination slot occupied: {to_date_str} {to_code}"); continue
            gen._unassign(pid, from_d, existing.shift)
            violations = gen._check_constraints(pid, to_d, to_shift)
            if violations:
                gen._assign(pid, from_d, existing.shift)
                descs = "; ".join(v.description for v in violations)
                rejected.append(f"Cannot move {name} to {to_date_str} {to_code}: {descs}"); continue
            gen._assign(pid, to_d, to_shift)
            result.assignments = [a for a in result.assignments
                                   if not (a.physician_id == pid and a.date == from_d and a.shift.code == from_code)]
            result.assignments.append(Assignment(
                date=to_d, shift=to_shift, physician_id=pid,
                physician_name=gen.submissions[pid].physician_name, is_claude=True,
            ))
            applied.append(f"Moved {name} from {from_date_str} {from_code} → {to_date_str} {to_code}")

    result.stats = gen._compute_stats(result)
    _state["result"] = result

    return AdjustResponse(
        schedule=_result_to_response(result),
        applied=applied,
        rejected=rejected,
    )


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
    ("DOC",       "Day On Call", None,    None),           # Day on call row
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
    ("NOC",       "Night On Call", None,  None),           # Night on call row
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


@app.post("/api/assign", response_model=ManualAssignResponse)
def manual_assign(body: ManualAssignRequest) -> ManualAssignResponse:
    """
    Manually assign a physician to a shift slot (human override).
    The assignment is force-applied even if rules are violated.
    Returns the list of rules that were broken for display.
    """
    gen: ScheduleGenerator | None = _state.get("generator")
    result: ScheduleResult | None = _state.get("result")
    if gen is None or result is None:
        raise HTTPException(status_code=400, detail="No schedule in memory. Generate first.")

    if body.shift_code not in ALL_SHIFT_CODES:
        raise HTTPException(status_code=400, detail=f"Unknown shift code: {body.shift_code!r}")

    if body.physician_id not in gen.submissions:
        raise HTTPException(
            status_code=400, detail=f"Physician {body.physician_id!r} not in current submissions."
        )

    try:
        d = datetime.date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {body.date!r}")

    # Find the Shift object
    shift_obj: Shift | None = None
    for block in BLOCKS:
        for s in block:
            if s.code == body.shift_code:
                shift_obj = s
                break
        if shift_obj:
            break

    violations = gen.assign_manual(body.physician_id, d, shift_obj)

    # Update the result: remove from unfilled if it was there, add to assignments
    sub = gen.submissions[body.physician_id]
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

    return ManualAssignResponse(
        success=True,
        violations=[ViolationSchema(rule=v.rule, description=v.description) for v in violations],
        message=(
            f"Assigned {sub.physician_name} to {body.shift_code} on {body.date}."
            + (f" {len(violations)} rule(s) overridden." if violations else " No rule violations.")
        ),
    )


# ---------------------------------------------------------------------------
# Claude API integration (global schedule improvement + unfilled slot fill)
# ---------------------------------------------------------------------------

def _get_anthropic_client():
    """Return an Anthropic client, or None if the library is unavailable."""
    try:
        import anthropic
        return anthropic.Anthropic()
    except ImportError:
        return None


def _claude_improve_schedule(
    result: ScheduleResult,
    gen: ScheduleGenerator,
    submissions: list[PhysicianSubmission],
    roster: dict,
) -> None:
    """
    Send the completed schedule to Claude for a full-context quality review.

    Claude receives all scheduling rules and physician statistics and is asked to:
      1. Suggest MOVE operations to improve schedule quality — each is validated
         against all hard constraints before being applied.
      2. For unfilled slots (if ≤5 remain), provide ranked candidate suggestions
         with clinical reasoning — these are stored as issues for human review
         and are NEVER auto-applied.

    This function never assigns a physician to an unfilled slot automatically.
    """
    client = _get_anthropic_client()
    if client is None:
        result.issues.append("Claude API unavailable (anthropic not installed).")
        return

    prompt = _build_improvement_prompt(result, gen)
    try:
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        result.issues.append(f"Claude API error during improvement pass: {exc}")
        return

    response_text = message.content[0].text
    moves_applied = 0

    for line in response_text.splitlines():
        line = line.strip()
        uline = line.upper()

        if uline.startswith("MOVE:"):
            parts = [p.strip() for p in line[5:].split("|")]
            if len(parts) != 5:
                continue
            name, from_date_str, from_code, to_date_str, to_code = parts
            try:
                from_date = datetime.date.fromisoformat(from_date_str)
                to_date   = datetime.date.fromisoformat(to_date_str)
            except ValueError:
                continue
            pid = next(
                (p for p, s in gen.submissions.items()
                 if s.physician_name.lower() == name.lower()), None
            )
            if pid is None:
                continue
            existing = next(
                (a for a in result.assignments
                 if a.physician_id == pid and a.date == from_date
                 and a.shift.code == from_code), None
            )
            if existing is None:
                continue
            to_shift = next(
                (s for block in BLOCKS for s in block if s.code == to_code), None
            )
            if to_shift is None:
                continue
            if any(a.date == to_date and a.shift.code == to_code
                   for a in result.assignments):
                continue
            gen._unassign(pid, from_date, existing.shift)
            violations = gen._check_constraints(pid, to_date, to_shift)
            if violations is not None:
                gen._assign(pid, from_date, existing.shift)   # restore
                continue
            gen._assign(pid, to_date, to_shift)
            result.assignments = [
                a for a in result.assignments
                if not (a.physician_id == pid and a.date == from_date
                        and a.shift.code == from_code)
            ]
            result.assignments.append(Assignment(
                date=to_date, shift=to_shift,
                physician_id=pid,
                physician_name=gen.submissions[pid].physician_name,
                is_claude=True,
            ))
            moves_applied += 1

        elif uline.startswith("RANK:"):
            # Ranked suggestion for an unfilled slot — append as informational issue.
            # Format: RANK: YYYY-MM-DD | shift_code | suggestions...
            rest = line[5:].strip()
            result.issues.append(f"💡 Claude suggestion: {rest}")

    if moves_applied:
        result.issues.append(f"Claude: {moves_applied} improvement move(s) applied.")


def _build_improvement_prompt(result: ScheduleResult, gen: ScheduleGenerator) -> str:
    """Build the full-context prompt for Claude's schedule review."""
    import calendar as cal_mod

    # Per-physician statistics
    counts: dict[str, int] = {}
    nights: dict[str, list[str]] = {}
    group_a: dict[str, int] = {}
    # Build consecutive-A sequences per physician for context
    pid_assignments: dict[str, list] = {}
    for a in result.assignments:
        counts[a.physician_id] = counts.get(a.physician_id, 0) + 1
        if a.shift.time == "2400h":
            nights.setdefault(a.physician_id, []).append(str(a.date))
        if a.shift.site_group.value == "A":
            group_a[a.physician_id] = group_a.get(a.physician_id, 0) + 1
        pid_assignments.setdefault(a.physician_id, []).append(a)

    # Detect A-A sequences per physician
    aa_violations: list[str] = []
    for pid, assigns in pid_assignments.items():
        sorted_assigns = sorted(assigns, key=lambda a: a.date)
        for i in range(1, len(sorted_assigns)):
            if (sorted_assigns[i].shift.site_group.value == "A"
                    and sorted_assigns[i-1].shift.site_group.value == "A"):
                name = gen.submissions[pid].physician_name
                aa_violations.append(
                    f"  {name}: {sorted_assigns[i-1].date} {sorted_assigns[i-1].shift.code}"
                    f" → {sorted_assigns[i].date} {sorted_assigns[i].shift.code}"
                )

    phys_lines = []
    for pid, sub in gen.submissions.items():
        n = counts.get(pid, 0)
        if n == 0:
            continue
        nights_list = sorted(nights.get(pid, []))
        night_dates = {datetime.date.fromisoformat(d) for d in nights_list}
        singletons = [
            str(d) for d in sorted(night_dates)
            if (d - datetime.timedelta(days=1)) not in night_dates
            and (d + datetime.timedelta(days=1)) not in night_dates
        ]
        a_count = group_a.get(pid, 0)
        a_pct = round(100 * a_count / n) if n else 0
        issues_flag = []
        if a_pct < 30:
            issues_flag.append(f"GroupA only {a_pct}% (target 40%)")
        if a_pct > 55:
            issues_flag.append(f"GroupA too high {a_pct}%")
        if singletons:
            issues_flag.append(f"singleton nights: {singletons}")
        flag_str = f" ⚠ {'; '.join(issues_flag)}" if issues_flag else ""
        phys_lines.append(
            f"  {sub.physician_name}: shifts={n}/{sub.shifts_requested}, "
            f"groupA={a_pct}%, 2400h={nights_list}{flag_str}"
        )

    unfilled_lines = []
    for u in result.unfilled:
        cands = ", ".join(
            f"{c.physician_name}({'; '.join(v.rule for v in c.violations)})"
            for c in u.candidates[:5]
        )
        unfilled_lines.append(f"  {u.date} {u.shift.code} — candidates: {cands}")

    month_name = cal_mod.month_name[result.month]
    few_unfilled = len(result.unfilled) <= 5

    rank_instruction = ""
    if few_unfilled and result.unfilled:
        rank_instruction = (
            "\n\nFor each unfilled slot, provide a ranked list of candidates. "
            "Output one line per slot:\n"
            "RANK: YYYY-MM-DD | shift_code | 1. Name (reason); 2. Name (reason); 3. Name (reason)\n"
            "Do NOT use MOVE to fill unfilled slots — only use RANK."
        )

    return f"""You are performing a full quality review of a {month_name} {result.year} emergency physician schedule.

HARD RULES (never suggest violating these):
- Minimum 22 hours between consecutive shifts for the same physician
- After a 2400h night shift, next shift must start at 1200h or later if returning the next day
- Maximum 3 consecutive working days
- Each physician has a maximum shift count they must not exceed
- Physicians cannot be assigned to forbidden sites

SOFT TARGETS:
- Each physician's shifts should be ~40% Group A (RAH A side / RAH B side), ~60% Group B
- 2400h night shifts should be clustered in runs, not spread as isolated singletons
- No two consecutive Group A assignments for the same physician in sequence
- Physician shift counts should match their requested count

CURRENT ISSUES:
Consecutive Group-A sequences ({len(aa_violations)}):
{chr(10).join(aa_violations) if aa_violations else "  None"}

PHYSICIAN STATISTICS:
{chr(10).join(phys_lines)}

UNFILLED SLOTS ({len(result.unfilled)}):
{chr(10).join(unfilled_lines) if unfilled_lines else "  None"}

TASK:
Suggest up to 10 specific MOVE operations that improve schedule quality while strictly respecting all hard rules above. Prioritise:
1. Fixing Group A/B imbalance for physicians flagged with ⚠
2. Clustering singleton 2400h nights
3. Breaking consecutive Group-A sequences

For each improvement output EXACTLY one line:
MOVE: physician_name | YYYY-MM-DD | from_shift_code | YYYY-MM-DD | to_shift_code

Moves will be validated against all hard constraints — violations are automatically rejected.
Only suggest moves where the physician is realistically available on the target date.
Do not suggest moves that obviously violate 22h spacing.{rank_instruction}
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5000, log_level="warning", access_log=False)
