# Feature roadmap — deferred items

Things identified during real work on the scheduler that are deliberately **not** being built yet. Each entry has enough context to pick back up cold.

---

## 1. Parse row 6 ("Preferred") — per-day shift/anchor preference

**Status**: deferred, not started. Identified 2026-09-22 while investigating a 2400h allocation asymmetry in `cpsat-oct12.xlsx` (RScheirer requested 6 nights, got 0).

**What it is**: physician submission sheets (`scheduler/backend/importer.py`'s layout) have a row 6 labeled "Preferred" that physicians fill in per-day — free text, e.g. `"N"` for "I'd prefer a night here specifically", or a literal shift code like `"16RA"` for a specific shift they want that day. Confirmed present and actively used in real submissions (RScheirer's October sheet has 6 days marked "N" and 2 marked with a specific code).

**Current state**: `importer.py` never reads row 6 at all — confirmed via full grep, no `_ROW_6` or row-6 reference anywhere in the parsing code. The signal exists in every submission file and is silently discarded before it ever reaches the solver. Today the solver only sees *monthly totals* (`shifts_2400h_requested`, `shifts_0600h_requested` — cells AK59/AK61) with zero information about which specific days a physician prefers those shifts to land on.

**Why it matters**: the anchor-fulfillment bonus (`generator_cpsat.py`, `_ANCHOR_FULFILLMENT_BONUS`) only rewards hitting the aggregate count — it has no way to prefer specific days. Adding row-6 as a per-day steering signal would likely improve both correctness (physicians actually get nights on days they can plan around) and the solver's ability to satisfy anchor requests at all, independent of the floor-mechanism work below.

**Scope this needs when picked up**:
- Add `_PREFERRED_ROW = 6` to `importer.py`, parse it per day (need to decide: is "N" the only night-marker convention, or are there other shorthand codes in use across the roster? — survey a sample of real submissions first, not just RScheirer's).
- Add a field to `DayAvailability` (e.g. `preferred_shift: str | None`) to carry it.
- Wire a soft per-day bonus into `generator_cpsat.py`'s objective when a physician's actual assignment matches their row-6 preference for that day.
- Check how many other physicians actually use row 6 (found via this investigation but not yet surveyed roster-wide) before sizing the real-world impact.

---

## 2. Anchor-fulfillment floor (2400h/0600h requests)

**Status**: agreed in principle 2026-09-22, not yet implemented. Blocked on nothing — could be built independently of item 1 above, though the two compound well together (item 1 gives the solver day-level steering, item 2 guarantees the aggregate count actually lands).

**Problem**: `generator_cpsat.py`'s anchor-fulfillment bonus is a flat sum term (+90 per 2400h/0600h shift, up to the physician's own requested count) with no per-physician floor. It's a real incentive but nothing stops the solver's global optimum from fully satisfying some physicians' anchor requests while zeroing out others, if the aggregate objective nets out similarly either way. Confirmed on real October 2026 data: RScheirer requested 6 2400h shifts (explicit, well-formed, genuine availability across 12+ days) and the actual CP-SAT solve — a 20-minute run, 1.1% optimality gap, so not a search-depth issue — gave him zero.

**Decided design direction**: a soft, heavily-weighted **lexicographic tier** (same pattern already used for the casual-priority tiers in `generator_cpsat.py`), not a hard `model.add(sum >= floor)` constraint. A true hard floor risks infeasibility if a physician's roster-level `only_0600h`/`only_2400h` flag ever conflicts with a stale/inconsistent monthly request for the type they can't work — see the conversation this was raised in. The lexicographic-tier approach can't deadlock: a physician whose hard constraints make their own floor unreachable just naturally contributes 0 to that tier, exactly like existing tiers already degrade gracefully today.

**Scope this needs when picked up**:
- New tier (or extend the existing casual-priority tier machinery) that maximizes total achieved anchor-fulfillment (or minimizes aggregate shortfall) across all physicians, run at high priority before the general fill-to-max tier.
- Needs its own weight/priority tuning pass against real data (same iterative process the existing anchor-overage-penalty comments describe — e.g. "250 wasn't enough, raised to 500").
- Decide whether the floor applies to 2400h and 0600h independently per physician (yes, per this conversation) or needs any interaction with `anchor_preference`.
- Re-run the corrected requested-vs-delivered analysis (see `2026-09-survey-analysis-plan.md`'s sibling notes, or redo fresh) against a build with this tier to confirm it actually closes the RScheirer-style gaps before considering it done.

---

## Analysis note (not a roadmap item, just context)

While investigating the above, found and fixed a real bug in the *analysis tooling* (not the production scheduler): a script excluding files matching `"Master Preferences"` in the October submissions folder silently dropped 3 physicians (Mason, Deol, Johnston) who only have that naming pattern, with no short-name duplicate. Corrected total 2400h demand for October 2026 is 126 (not 120), and the "went to non-requesters" figure is 7 shifts (Bly + EChang), not the originally-reported 32 — most of the physicians in that first bad list were real requesters wrongly zeroed out by the filter, not genuine anomalies. RScheirer's 0-of-6 finding is unaffected by this correction and remains real.
