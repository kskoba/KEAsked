# Implementation Roadmap

## Phase 1: Discovery and Modeling

Goal: convert your real-world process into explicit machine-readable rules.

Deliverables:

- sample input files collected
- shift taxonomy documented
- rule inventory split into hard vs soft constraints
- initial schema for physicians, shifts, and requests

## Phase 2: Import and Validation

Goal: reliably read physician submissions and catch bad data early.

Deliverables:

- Excel parser
- validation errors with clear messages
- normalized JSON-like internal representation

Examples of validations:

- unknown physician name
- duplicate rows
- invalid shift code
- impossible date values
- missing availability markers

## Phase 3: Solver MVP

Goal: produce a valid monthly schedule for a simplified dataset.

Initial hard constraints:

- unavailable means never schedule
- at most one shift per physician per day
- every shift filled exactly once, or marked unfilled if allowed
- physician monthly max enforced

Initial soft constraints:

- requested shifts rewarded
- avoid too many consecutive shifts
- prefer target monthly totals
- prefer minimum spacing between shifts

## Phase 4: Explainability

Goal: make the scheduler understandable and auditable.

Deliverables:

- score breakdown by physician
- unfilled shift explanations
- report of unmet requests
- fairness metrics

This matters because trust will determine adoption.

## Phase 5: Review UI

Goal: let the scheduler be reviewed and lightly adjusted by humans.

Core screens:

- import data
- validation issues
- run scheduler
- inspect generated schedule
- export schedule

## Key Design Choice

Do not encode all rules directly inside UI code or spreadsheet logic.

Instead, put rules in:

- Python solver code for structural constraints
- versioned config files for adjustable weights/limits

That gives you auditability and easier iteration.

## Repository Recommendation

Suggested layout:

```text
scheduler/
  README.md
  docs/
  samples/
  backend/
    scheduler/
    tests/
  desktop/
```

## Proposed First Build Sprint

Sprint 1:

- create repo
- add sample files
- define schema
- implement Excel import
- produce normalized preview

Sprint 2:

- build small solver
- test against toy scenarios
- validate hard constraints

Sprint 3:

- tune soft constraints
- export schedule
- add reporting

Sprint 4:

- add desktop interface
- package for Windows/macOS

## Practical Advice

If the group currently depends on one scheduler's judgment, capture that knowledge explicitly now.

The biggest risk is not the coding. It is hidden scheduling rules that only exist informally in someone's head.
