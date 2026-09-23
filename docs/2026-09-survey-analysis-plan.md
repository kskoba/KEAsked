**Status (2026-09-22, later same day)**: Rules 1, 2, 3, 6, 7 approved and implemented — schema in `scheduler/backend/config.py`, solver wiring in `scheduler/backend/generator_cpsat.py` (rules 1/3/6/7) and `scheduler/backend/generator.py`'s `assign_on_calls` (rule 2), values assigned to the 19 physicians who explicitly asked for them (see per-rule notes below for exact wording/confidence). Rule 2's "before a block" option was narrowed at implementation time, per correction: only ever DOC immediately before an evening-start shift (`call_linkage="doc_before_evening"`) — never a general "before," never NOC. Rules 4, 5, 8 not yet approved. **Part 1 (direct-mapping physicians.yaml diffs) is still pending your go-ahead — not yet applied.**

# Shift Preferences Survey (Google Forms) — analysis & rule proposal

Source: `~/Downloads/Shift Preferences Survey KEA (Responses).xlsx`, 46 responses (of 100 active-ish roster entries), collected before the sked annual survey existed. Resolved against `scheduler/config/physicians.yaml` using the codebase's own `physician_resolver.build_alias_index()` — 41/46 auto-resolved; 5 required manual disambiguation on first-name-only or nickname mismatches (Jessie→Breton, Cameron MacGougan→MacGougan, Amy→Hegstrom, Jim Rogers→Rogers, Cristina→Garcea). Recommend adding these as `aliases` in physicians.yaml so future imports resolve automatically.

Two respondents submitted a single joint response under one name that resolves to **two separate roster IDs**: "Kenneth Lam and Michelle Rico" → `KLam` + `MRico` (both currently independent identities, not merged — matches the Sept 12 working note). Their answers apply to both IDs.

## Method

1. Read every free-text answer for all 46 respondents in full (not sampled).
2. Computed distributions for the 15 Likert-scale "importance" items (5-point ordinal: Not important(1) → Never break(5)).
3. Cross-referenced every respondent's stated numeric preferences against their *current* `physicians.yaml` values (`max_consecutive_shifts`/SIAR, `max_consecutive_nights`/NIAR, `prefer_singleton_nights`, `forbidden_shift_times`, `rest_after_late_shift`, `avoid_mondays`, etc.) using `scheduler.backend.config.load_roster()`.
4. Checked which `PhysicianConfig` fields are actually wired into `generator_cpsat.py` vs. present-but-unimplemented vs. genuinely absent, so "new rule" proposals are scoped against what's mechanically feasible.

## Likert importance ranking (mean, 1–5 scale; n≈43-45, ~1-3 skips each)

| Item | Mean | %rated 4-5 | %rated "never break" |
|---|---|---|---|
| Cap of 2 weekends worked/month | 3.84 | 70% | 44% |
| Fair share of "undesirable" shifts | 3.81 | 72% | 28% |
| Call shift on an otherwise-off weekend | 3.58 | 58% | 31% |
| More than requested shifts, multi-month in a row | 3.43 | 50% | 27% |
| Cool-down/rest period between blocks | 3.39 | 52% | 25% |
| More/less than max/min requested total shifts | 3.27 | 51% | 13% |
| Assigned a disliked shift type | 3.20 | 49% | 7% |
| Assigned more than requested shifts-in-a-row | 3.18 | 51% | 7% |
| More than requested 0600/2400 (within 40% share) | 3.09 | 40% | 7% |
| Single isolated mid-week shift | 2.96 | 42% | 11% |
| Single isolated weekend shift | 2.86 | 34% | 11% |
| Less than requested max shifts-in-a-row | 2.67 | 28% | 14% |
| Protected/specific admin day | 2.53 | 26% | 14% |
| Variety in shift times | 2.51 | 12% | 0% |
| Balance shifts evenly across weeks | 2.37 | 14% | 2% |

**Note**: "Cap of 2 weekends/month" is already the global default (`scheduler_config.yaml: max_weekends_per_month: 2`) — the #1-rated concern is already satisfied by existing config, not a gap.

---

## Part 1 — Direct mappings: physicians.yaml updates (existing fields, no new rule needed)

High-confidence mismatches between what a physician told us and what's currently in the roster file. All use fields that already exist and are already solver-enforced.

### `max_consecutive_nights` (NIAR) corrections — currently defaults to SIAR when unset; several respondents want it strictly lower

| id | current NIAR | survey says | evidence |
|---|---|---|---|
| Esterhuizen | 3 | **1** | "I do not want to work two nights in a row... totally crumble with 2 nights in a row" |
| Fisher | 4 | **2** | "Only 2 nights in a row" |
| Tiessen | 4 | **2** | "of the 2000 shift and midnights, only max 2 of those in a row" |
| Rawe | 3 | **1** | "I can only do 1 night at a time" |
| Sachs | 3 | **1** | "Only one 2400h shift in a row please" |
| LamN | 3 | **2** | "can't do three 2400s in a row" |
| Meleshko | 3 | **2** | "only two nights... not three nights in a row" |
| Lung | 3 | **2** | "only 2 nights in a row" |
| Rogers | 4 | 3 (medium confidence) | "If doing a run of straight 2400s prefer only 3" |

### `max_consecutive_shifts` (SIAR) corrections

| id | current | survey says | evidence |
|---|---|---|---|
| KLam / MRico | 5 | **4** | explicit "4" |
| Johnston | 5 | **3** | explicit "3" |
| Taylor | 4 | 3 (medium) | "If doing 2-3 in a row..." |
| Lefebvre | 4 | 3 (medium) | explicit "3" |
| Edgecumbe | 4 | 3 (medium) | "I can do three" (2 preferred) |
| Kjelland | 2 | **3** | "2 in a row - 3 max" (raise, not lower) |

### Other existing-field updates

| id | field | change | evidence |
|---|---|---|---|
| Johnston | `avoid_mondays` | False → **True** | NE Lead, "trying to keep most Mondays available as an administrative day" — this is the *exact* existing mechanism |
| Velji | `forbidden_shift_times` | add `0600h` | "I don't do 0600s so do extra nights to compensate" |

**Schema gap found while doing this**: `only_2400h` exists as a hard boolean but there's no symmetric `only_0600h`. At least 4 respondents are effectively 0600-only (Mrochuk: *"Only 0600hs please... 100% of the time"*; Deol: *"only 0600"*; Garcea: *"doing all 0600 these days... only 2 shifts you can do exclusively"*; Lali largely). Recommend adding `only_0600h: bool` (mirrors `only_2400h`) as a trivial schema-symmetry fix, then setting it for these physicians — this is barely a "new rule," just completing an existing asymmetry, but flagging alongside the new-rule list below since it's still a schema change.

*(This table covers the clearest, best-evidenced cases from a full read of all 46 responses — not an exhaustive per-field pass on every respondent. Once you confirm the approach, a second pass can pull out the remaining smaller nuances, e.g. exact shift-type dislikes, most of which will actually be better served by new rule #4 below once it exists.)*

---

## Part 2 — Already-specified, just not wired into the solver (no new decision needed)

- **`max_shifts_per_week`** — already in `sked`'s `RULE_CATALOGUE.ts` (allowHard+allowSoft) but zero references in `generator_cpsat.py`. Erica Dance explicitly wants "not more than 3 shifts in any 7-day period." Recommend implementing.
- **`max_consecutive_0600`** — same story: in the sked catalogue, explicitly flagged there as "not yet enforced," and has no `PhysicianConfig` field at all. Nobody explicitly asked to cap 0600-in-a-row this round, so lower priority than the above.

---

## Part 3 — Candidate new rules (need your sign-off before I build any of these)

Ranked by strength of evidence. Each notes the mechanism (using fields the solver already has access to, where possible) and roughly how many of the 46 respondents' free text explicitly supports it.

**1. Post-block cool-down (rest days after a block of N+ shifts)** — ~14 respondents explicit (Esterhuizen 48h, Taylor 48h, Lefebvre 48h "essential", Schonnop 48h+3-days on a 2400→0600 transition, Lucyk 72h, Rawe 2 days after a midnight, LamN 48h, Meleshko 2 days, Sachs "a few days"). Generalizes the existing `rest_after_late_shift` mechanism (currently only triggers on specific late-shift *times*) to trigger on *block length* instead. Parameters: `min_block_len_trigger`, `rest_days_required`.

**2. Call-shift linkage** — ~11 respondents explicit, genuinely multi-directional (Yeung/Taylor/MacGougan/Rogers/Scheirer/Schonnop want it *linked to end of block*; KLam/Rico want it *before* a block specifically; Norum wants it *independent*; AHanson: fine floating but not on an otherwise-off weekend). Currently zero linkage logic exists — `assign_on_calls` is a standalone post-process. Parameter: `call_linkage: "end_of_block" | "start_of_block" | "independent" | "no_preference"` — a genuinely bimodal rule since it can satisfy opposing preferences simultaneously.

**3. No repeated site in a row** ("no 2 intake/NE shifts back to back") — ~9 respondents (Boyd, Chad Lucyk, Tiessen, Rawe, Nina Lam, Kjelland, Amanda Hanson via "last shift not acute"). Reuses the *existing* `Shift.site` / `Shift.site_group` fields — no new taxonomy needed. Parameter: `max_consecutive_same_site: N` (or by `site_group`).

**4. Soft preferred/avoided shift-type list** — the single highest-volume theme (~25+ respondents named specific liked/disliked shift types in free text) and inherently bimodal: what one person calls undesirable (0600, evenings, intake, 2400) another explicitly loves (Samoraj: "LOVE float shifts"; Dong: prefers 0600 specifically *because* others avoid it). Distinct from the existing hard `forbidden_shift_times` — this is a soft, points-weighted nudge, closer to how sked's own standing-preference soft rules already work. This directly answers your "fair share of undesirable shifts" (L15, 72% important) and "assigned a disliked shift" (L02) Likert items. Highest-leverage single rule given how much natural variance exists across the group.

**5. Cascading/ascending shift-start-time preference** — ~9 respondents (Breton, Velji, Taylor, Rogers, Hegstrom, Sachs, Meleshko all want shifts to run earlier→later across a block, e.g. 1800→2000→2400). Reuses the existing `Shift.start_hour` property directly — cheap to implement as a soft objective term.

**6. Generalize `avoid_mondays` → `avoid_weekday: <day>`** — cheap refactor (one existing mechanism, currently hardcoded to Monday) that would directly satisfy Schonnop (Wednesday admin day) in addition to Johnston (already covered by the existing field — see Part 1).

**7. Weekend-clumping preference** — ~4 respondents want their weekend shifts concentrated onto the fewest distinct weekends rather than spread thin (Tiessen: "rather do Fri/Sat/Sun all on one weekend... than 1 shift/weekend over 2 weekends"; Rawe; Kjelland wants the opposite phrased as "not multiple weekends with one shift only" — same underlying ask). Distinct from `max_weekends` (a cap on count) — this is about concentration, not quantity.

**8. Extend singleton-shift preference beyond 2400h nights** — moderate/bimodal signal from Likert (L03/L04) but weaker free-text conviction than 1-7 (most say "fine either way"; only a handful — Bre­ton, Dennis Lefebvre, Matemisz — actively dislike isolated shifts of *any* type, vs. many who are neutral). Would extend the existing `prefer_singleton_nights` machinery to other shift types/days. Lower priority — could fold into rule #4 (soft avoid) rather than its own dedicated rule.

That's 8 candidates (7 if #6 is treated as a trivial extension of an existing field rather than "new"), leaving headroom under your 10-rule ceiling for anything that comes out of a second look, or for splitting #4 into separate "preferred" / "avoided" entries if you'd rather weight them independently.

**Explicitly NOT recommended as one of the 10**: a systemic "equitably distribute under-filled shift types across the whole roster" objective (raised by Lung, Ron Singh, Eddie Chang) — this is a *global fairness objective*, not a per-physician toggle, so it doesn't fit the same points-budget mechanism as the others. Worth a separate conversation about the CP-SAT objective function itself, not a `physicians.yaml` rule.

---

## Part 4 — Found, but out of scope for "physicians.yaml rules" (flagging separately)

- **Paired/coordinated (but not merged) scheduling** — Boyd Edgecumbe + Kendra Houston explicitly (both confirm independently) want complementary-not-overlapping schedules for childcare, distinct from KLam/MRico's fully-merged-identity approach. This needs a genuine *pairwise* joint constraint between two specific physicians, which is a different class of feature from a single-physician weighted preference. Worth its own design discussion.
- **"Gaming via ultra-narrow availability"** — raised independently by Edgecumbe, Lucyk, Rawe, Peterson, and Eddie Chang: some physicians submit very tight availability to force only-preferred shifts, leaving flexible physicians to absorb the rest. This is a submission-*validation* policy question (tightening `min_valid_days`/`min_blocks_per_day` floors), not a scheduling preference.
- **Annual on-call quota enforcement** (Ron Singh) — a policy/audit feature (track over a year, not a month), not a per-solve constraint.
- **Learner-schedule visibility in the app** — by far the single most repeated piece of feedback across the whole survey (Velji, Anderson, Keyes, Taylor, Norum, Edgecumbe, Fisher, Kjelland, Sachs, Houston — 10 of 46 unprompted). Not a scheduling rule at all — a product feature request for the KEAsked app itself. Worth prioritizing given the frequency.
- A few respondents (Chad Lucyk, Amanda Hanson) suggested AI-assisted scheduling or process tweaks (2-month vs 3-month submission lead time) — noted for awareness, no action proposed.

---

## What I need from you

1. **Part 1 diffs** — apply as-is, or review first? (My NIAR inferences are read directly from explicit statements, but I haven't cross-checked every one against a human scheduler's institutional knowledge of *why* a value might be intentionally different.)
2. **Part 3 candidates** — which of the 7-8 should I actually build? Each is independent; you can approve any subset.
3. Anything in Part 4 you want turned into an actual task (especially the learner-schedule visibility one, given how often it came up).
