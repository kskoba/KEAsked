# KEAsked Scheduling System — Architecture Reference

## Overview

KEAsked is a desktop-based emergency physician scheduling system combining:
- **Electron** desktop app (Windows/macOS) with React frontend
- **Python FastAPI** backend solver running as a subprocess
- **Constraint-based scheduling** with two solver modes: greedy heuristic and CP-SAT optimization

The system ingests monthly physician shift requests (Excel), validates them against explicit rules, and generates optimized schedules respecting hard constraints (safety/availability) and soft preferences (fairness, continuity, site preferences).

## High-Level Architecture

```
┌─────────────────────────┐
│   Electron Main App     │
│  (frontend/src/main/)   │
└──────────────┬──────────┘
               │ spawns subprocess
               ↓
┌─────────────────────────────┐
│  FastAPI Server (port 5000) │ ← HTTP REST endpoints
│  (scheduler/api/server.py)  │
└──────────────┬──────────────┘
               │
        ┌──────┴──────┬──────────────────┐
        ↓             ↓                  ↓
    ┌────────┐  ┌──────────┐  ┌──────────────────┐
    │ Import │  │ Validate │  │ Solver (choose)  │
    │ (Excel)│  │(4 rules) │  │  ├─ Greedy       │
    └────────┘  └──────────┘  │  └─ CP-SAT       │
                               └──────────────────┘
```

**Workflow:** Import Excel submissions → Validate → Generate schedule → Manual review → Export XLSX

---

## Component Glossary

| Component | Path | Role |
|-----------|------|------|
| **Importer** | `scheduler/backend/importer.py` | Parse Excel submissions into `PhysicianSubmission` objects |
| **Validator** | `scheduler/backend/validator.py` | Check submission rules; support per-physician `rule_overrides` |
| **Greedy Solver** | `scheduler/backend/generator.py` | Fast heuristic solver (~1–2 sec) |
| **CP-SAT Solver** | `scheduler/backend/generator_cpsat.py` | OR-Tools optimization; near-optimal solutions (10–90 sec) |
| **FastAPI Server** | `scheduler/api/server.py` | HTTP REST layer; chooses solver; exports XLSX |
| **Config Loader** | `scheduler/backend/config.py` | YAML parsing for physicians & scheduler rules |
| **Models** | `scheduler/backend/models.py` | Dataclass definitions (submissions, assignments, results) |
| **Shifts** | `scheduler/backend/shifts.py` | Shift taxonomy (sites, times, blocks, group A/B) |
| **React Frontend** | `frontend/src/renderer/` | Desktop UI (import → validate → schedule → adjust → export) |

---

## Where to Adjust Per-Physician Settings

**File:** `scheduler/config/physicians.yaml`

Changes here take effect immediately — no server restart needed.

```yaml
physicians:
  - id: "Braun"                          # must match Excel submission filename/name
    name: "Braun"
    active: true                         # false → excluded from scheduling entirely

    scheduling:
      max_consecutive_shifts: 2          # hard cap on consecutive working days (default: 3)
      max_consecutive_nights: 2          # hard cap on consecutive 2400h shifts
      max_weekends: 2                    # max occupied Fri/Sat/Sun clusters
      group_b_site_preference: "rah"     # within Group B: "nehc", "rah", or "rah_f"
      only_2400h: true                   # restrict physician to overnight shifts only
      honor_all_requests: true           # +150 weight bonus — forces all requested shifts
      cap_at_requested: true             # hard max = shifts_requested (no extras)
      prefer_singleton_nights: false     # if true, penalises consecutive 2400h runs
      prefer_weekends: false             # bonus for Fri/Sat/Sun assignment
      avoid_mondays: false               # apply Monday penalty
      rest_after_late_shift: false       # mandatory rest after 1800h+ shift

    forbidden_sites:                     # physician can never be assigned here
      - "NEHC"
      - "RAH I side"

    forbidden_shift_times:               # physician can never work these times
      - "0600h"

    rule_overrides:                      # override submission validation thresholds
      min_valid_blocks: 1                # default: n×4
      min_valid_days: 2                  # default: ceil(n×1.5)
```

**Template for new physicians:** `scheduler/config/physicians_template.yaml`

---

## Where the Overall Scheduling Rules Live

**File:** `scheduler/config/scheduler_config.yaml`

Changes here take effect immediately — no server restart needed.

| Section | Field | Default | Meaning |
|---------|-------|---------|---------|
| `site_distribution` | `group_a_target` | 0.40 | Target 40% RAH A/B (acute) per physician |
| | `group_b_target` | 0.60 | Target 60% RAH I/NEHC/F (non-acute) per physician |
| | `tolerance` | 0.05 | ±5% acceptable deviation |
| `spacing` | `min_hours_between_shifts` | 23 | Minimum hours between shift start times |
| `consecutive` | `max_consecutive_shifts` | 3 | Default consecutive-day hard cap |
| `anchor_shifts` | `anchor_max_total` | 4 | Max combined 0600h + 2400h when not specified |
| `weekends` | `max_weekends_per_month` | 2 | Max occupied weekend clusters (Fri/Sat/Sun) |
| `timed_separation` | `physicians` | — | Pair constraints: e.g. Brenneis & Fanaeian ≥6h apart |
| `conditional_cowork` | `physicians` | — | Conditional constraints between specific physician pairs |

---

## Where to Tweak Monthly Requests (Excel Importer)

**Parser:** `scheduler/backend/importer.py`

Physicians submit one Excel file per month with a **fixed cell layout**:

| Data | Row | Column | Notes |
|------|-----|--------|-------|
| Physician name | 1 | A | Used for roster lookup |
| Shifts requested (N) | 38 | AK | Integer — primary target |
| Min shifts | 40 | AP | Optional floor |
| Max shifts | 42 | AP | Hard cap (≤ N+2) |
| Requested 2400h count | 59 | AK | Used for anchor target |
| Requested 0600h count | 61 | AK | Used for anchor target |
| Block availability | rows 8–29 | B–AE (days 1–31) | Non-empty = available |

**Block layout (rows 8–29):**

| Block | Rows | Times | Sites |
|-------|------|-------|-------|
| 0 | 8–11 | 0600h | RAH A, RAH B, NEHC, RAH I |
| 1 | 12–16 | 0900–1200h | NEHC, RAH I, RAH A, RAH B, RAH F |
| 2 | 17–21 | 1400–1700h | RAH I, NEHC, RAH F |
| 3 | 22–25 | 1800–2000h | RAH A, RAH B, RAH I, NEHC |
| 4 | 26–29 | 2400h | RAH A, RAH B, NEHC, RAH I |

**Submission validation rules** (`scheduler/backend/validator.py`):
1. ≥ `ceil(N × 1.5)` valid days (has Z flag + ≥2 blocks marked)
2. ≥ `N × 4` valid blocks total
3. ≥ `ceil(N × 0.6)` valid weekend days
4. ≥ `ceil(N / 2)` anchored days (days with 0600h or 2400h blocks)

Per-physician `rule_overrides` in `physicians.yaml` can disable or lower any of these thresholds.

---

## Solver Weights & Penalties

### CP-SAT Solver — Soft Objective Terms
**File:** `scheduler/backend/generator_cpsat.py` lines 559–800

| Term | Weight | Meaning |
|------|--------|---------|
| Filled slot baseline | +1000 | Primary objective — fill as many slots as possible |
| Requested shift (normal) | +5 | Physician marked this specific shift |
| Requested shift (`honor_all`) | +150 | Force-assign requested shifts (+1053 total with baseline) |
| Requested-count bonus | +50 | Each shift up to N (requested count) |
| Beyond-requested fill | +3 | Marginal reward for shifts beyond N |
| Group A floor bonus | +30 | Each A-side shift up to 40% target |
| Group B site preference match | +6 | Physician's preferred B-side site matched |
| A→B or B→A consecutive pair | +20 | Alternation between acute/non-acute sides |
| A→A consecutive pair | −25 | Heavy penalty for two acute shifts back-to-back |
| 2400h consecutive pair | +18 | Clustering bonus (unless `prefer_singleton_nights`) |
| Consecutive working pair | +10 | Reward for extending an existing run |
| 2400h pair (`prefer_singleton`) | −30 | Penalty when physician prefers isolation |
| 4+ consecutive-day run | −35 | Penalty for long runs on high-mc physicians |

### Greedy Solver — Scoring Factors
**File:** `scheduler/backend/generator.py` lines 1059–1219

| Factor | Weight | Meaning |
|--------|--------|---------|
| Service day Z flag | +5 | Physician marked this day as available |
| Group A/B balance correction | ±80 | Corrects deviation from 40/60 target |
| Minimum deficit (fractional) | +30 | Unmet minimum ratio |
| Requested deficit (fractional) | +10 | Unmet requested ratio |
| Small-request protection | +12 | Extra bonus for physicians requesting ≤5 shifts |
| `honor_all_requests` | +50 | Force-assign requested shifts |
| A→A consecutive penalty | −30 | Same as CP-SAT |
| 2400h run bonus | +6 | Extend existing night run |
| 2400h isolation penalty | −5 | Avoid new isolated overnight |
| Pacing penalty | −2.5× | Prevent front-loading |

---

## Hard Constraints (Cannot Be Violated)

Enforced in both solvers. Violation results in an **unfilled slot** (not an invalid schedule).

| # | Rule | Source |
|---|------|--------|
| HC-1 | ≤1 physician per shift slot | Shift uniqueness |
| HC-2 | ≤1 shift per physician per day | Single-assignment |
| HC-3 | Availability (block-level from Excel) | Physician submission |
| HC-4 | Forbidden sites (from `physicians.yaml`) | Roster config |
| HC-5 | `only_2400h` restriction | Roster config |
| HC-6 | Max shifts hard cap | Submission + roster |
| HC-7 | ≥23h between consecutive shift start times | `scheduler_config.yaml` |
| HC-8 | Max consecutive working days | Roster/global config |
| HC-9 | Mandatory rest after 2400h shift | Safety rule |
| HC-10 | Max consecutive overnight (2400h) shifts | Roster config |
| HC-11 | Anchor shift total cap (0600h + 2400h) | Roster/global config |
| HC-12 | Weekend cluster limit | Roster/global config |

---

## Two Solvers Compared

| | Greedy (`generator.py`) | CP-SAT (`generator_cpsat.py`) |
|--|------------------------|-------------------------------|
| **Speed** | 1–2 seconds | 10–90 seconds (configurable) |
| **Quality** | Good (~97–98% fill) | Near-optimal (~98–99% fill) |
| **Strategy** | Heuristic, slot-by-slot | Integer programming (OR-Tools) |
| **Optimality** | No guarantee | Reports optimality gap % |
| **Randomization** | Seed parameter for variety | Parallel search workers |
| **Fallback** | Always available | Falls back to greedy if OR-Tools missing |

---

## Key Configuration Files Summary

| File | Purpose | Restart Required? |
|------|---------|-------------------|
| `scheduler/config/physicians.yaml` | Per-physician overrides, forbidden sites/times, soft preferences | No |
| `scheduler/config/scheduler_config.yaml` | Global rules: targets, spacing, weekend limits, pair constraints | No |
| `scheduler/backend/generator_cpsat.py` | CP-SAT objective weights and hard constraint logic | Yes (code change) |
| `scheduler/backend/generator.py` | Greedy scoring weights | Yes (code change) |
| `scheduler/backend/importer.py` | Excel cell layout constants | Yes (code change) |
| `scheduler/backend/validator.py` | Submission validation rule thresholds | Yes (code change) |

---

*Document version: 2026-03-27 — Applies to KEAsked with CP-SAT and Greedy solvers*
