# KEAsked — session handoff (updated 2026-10-07)

Picking up on a different machine / fresh chat? Read this whole file first —
it's written for a session with zero memory of prior conversations.

## ⭐ Next task — start here

**Cross-month continuity is now wired end-to-end (2026-10-07, uncommitted
as of this writing — check `git status`).** The remaining step before a
60-minute run is to get a *real* finalized December schedule as a local
xlsx and load it; everything else is built and tested (76/76 backend tests).

**What was built 2026-10-07 (this session):**
- `scheduler/backend/trailing.py` (new, pure functions): `previous_month`,
  `trailing_window_days` (= max(max_consecutive_shifts,
  max_consecutive_nights, 3)), `build_trailing_assignments` (regular shifts
  only, DOC/NOC excluded, hard `ValueError` if the prior ScheduleResult
  isn't actually the previous calendar month), plus the month-to-month
  carry-over pieces below.
- `/api/generate` now resolves prior-month data and passes
  `trailing_assignments=` + `prior_month=` into `CpsatScheduleGenerator`.
  Sources in priority order: (1) `trailing_file` in the generate body
  (one-shot xlsx path), (2) whatever was loaded via
  `POST /api/trailing-schedule`, (3) the master Google Sheet if configured.
  Any failure degrades to None with a reason recorded in
  `_state["trailing_last_used"]` (visible on `GET /api/trailing-schedule`
  and printed as `[trailing] ...`) — never a hard failure.
- New routes: `POST /api/trailing-schedule` loads the prior month into its
  own `_state["trailing_source"]` slot (does NOT touch the active month's
  result/year/month); `GET`/`DELETE /api/trailing-schedule` inspect/clear it.
  Schedule source is exactly one of `file` (exported xlsx; month read from
  its sheet title) or `sheet_url` + `year` + `month` (a master-sheet link:
  read through the Sheets API when `google_sheets.yaml` exists, otherwise
  via the public xlsx export, which needs the sheet shared "anyone with the
  link can view"). Requested counts (for repeat overage) come from at most
  one of `preferences_directory` (that month's preference xlsx folder) or
  `sked_period_id` (e.g. `"2026-12"`, pulled with the same fetch loop
  `/api/sked/import` uses, refactored into `_fetch_sked_period_submissions`).
- **Steady state once credentials exist**: no manual loading at all. The
  generate-time fallback finds the previous month's master sheet by name in
  the configured Drive folder (that sheet is what the app's own "Push to
  Master Sheet" writes) and pulls that month's requested counts from sked
  period `YYYY-MM`. Tested with both integrations stubbed. For now (no
  `google_sheets.yaml` on this machine) the user's stated plan is: paste the
  December master-sheet link + point at the December requests folder.
- **Month-to-month carry-over metrics** (user request, 2026-10-07), both
  soft objective terms in `generator_cpsat.py`, inert without prior data:
  - *Acute debt*: a physician whose prior month's acute (RAH A/B) share
    fell below the Group A target by ≥1 whole shift gets one extra acute
    unit this month at weight 120 (`_ACUTE_CARRYOVER_WEIGHT`), on top of
    the normal 30-per-unit A-floor target. Skipped when every acute site
    is in `forbidden_sites` (Krisik, Francescutti) via
    `trailing.acute_eligible`.
  - *Repeat overage*: a physician scheduled over their requested count
    last month pays 150 (`_OVERAGE_CARRYOVER_PENALTY`) per shift beyond
    this month's requested count — redirects overage to someone else when
    there's a choice, never leaves a slot empty. **Needs the prior month's
    requested counts**, which only the prior month's submission xlsx files
    carry — pass `preferences_directory` (the December preferences folder)
    when loading the trailing schedule, or this term stays inert and the
    status route reports `requested_known_count: 0`.
- `sheets_schedule_reader.py` split: `parse_schedule_grid` (the grid walk,
  now source-agnostic), `parse_schedule_from_spreadsheet_id`,
  `spreadsheet_id_from_url`, `download_public_sheet_grid` (credential-free
  export download; a Google sign-in HTML page is detected and reported).
- Tests: `test_trailing.py` (25: helpers, real xlsx round trip through
  `_build_export_workbook` → `_parse_schedule_xlsx` → solve, routes +
  resolver incl. active-state isolation, graceful degradation, sheet-link
  and sked sources, and the fully-automatic fallback) and
  `test_carryover.py` (10: summaries, and *contested mirrored* solver tests
  where two identical physicians differ only in last month's record).

**Also 2026-10-07 (afternoon), while preparing real December data:**
- **Float shift hours corrected**: the app's `EXPORT_SHIFTS` row said
  `RAH Float 1600-0459` (the on-call END time, copied from NOC). Real hours
  are **1600-2400** (user-confirmed). Export now writes 1600-2400; the old
  label is kept as a read-only back-compat alias so pre-fix exports still
  load.
- **Master-sheet reader fixed for the REAL department sheet** (found by
  parsing `Tests/2026-12 - December 2026 RAH NECHC Master Schedule.xlsx`,
  an export of the real sheet): (a) no blank spacer row between weeks ->
  the old parser swallowed every other week (got 320 of 651 slots); (b) a
  blank date cell on Dec 1 and a wrong-year datetime on the spill-over
  Jan 1 cell -> dates now inferred from neighbours by column offset; (c)
  on-call rows are labelled `AM CALL`/`PM CALL`, not DOC/NOC -> aliases +
  `CALL_TYPE_BY_LABEL` canonicalisation; (d) stray whitespace in labels.
  Same week-boundary fix applied to `_parse_schedule_xlsx`. Now parses all
  651 slots (630 filled, 21 unfilled, 50 on-calls). Tests in
  `test_master_sheet_reader.py`. Full suite 76/76.
- **`Lam-Rico` alias added to KLam** in `physicians.yaml` (gitignored real
  config -- needs `./scripts/sync-config-to-unraid.sh` to reach Unraid).
  The master sheet records the shared Lam/Rico position under that name
  and the reader hard-errors on an unresolved name.
- **December preference folder**: Zhang's submission was the anonymous
  Numbers-exported `.xls`; converted (values only, via xlrd) to
  `Zhang - December 2026.xlsx`, imports cleanly (10/9/10). Still **no
  December preference sheet** for 7 physicians who worked December per the
  master sheet: Deol (6 shifts), Johnston (9), Mason (7), Rawe (6),
  Rozmahel (1), Sharma (9), Thirsk (6, PDF-only). Their requested counts
  are unknown, so the repeat-overage carry-over is inert for them; the
  acute-debt term still works (needs only the schedule).
- **`scripts/kea.sh`** -- terminal control of the dev stack
  (`kea up|down|backend|status|logs|test`); alias
  `kea='/home/cid/Dropbox/KEAclaude/KEAsked/scripts/kea.sh'`. Refuses to
  stop a backend with a solve in flight unless `--force`.
- **Unraid is stale**: `http://192.168.0.5:5000/api/trailing-schedule`
  returns 404 (container predates this session). Needs: user `git push`,
  then the `update-unraid-backend` procedure (SSH -- ask first), plus
  config sync for the KLam alias, plus `sync-preferences-to-unraid.sh` for
  whichever month folder is used as `/config/<Month>`.

**Also 2026-10-07 (afternoon, from the user's review of `Tests/cpsatv2-jan1.xlsx`):**
- **Anchor-share fairness** (`generator_cpsat.py`, config `anchor_shifts.
  anchor_share_target: 0.40`, `anchor_min_floor_full_threshold: 8`). Root
  cause found in that solve: a BLANK 0600h cell was free (no cap, no
  penalty) while an explicit 0 cost 500/unit -- so Garcea (blank) got 8 of
  8 shifts as 0600h, Bacon 60% anchors, while 0/0 physicians with 8 shifts
  (Wittmeier, Thirsk) sat at 1. Now every physician has `anchor_target =
  max(stated anchor requests, floor(0.40 x requested))`: unrequested
  anchors within it cost 150 (60 for their preferred type), anchors beyond
  it cost 500/800/1100 escalating -- for everyone, blank or stated. Stated
  per-type hard caps are `max(request + tolerance, anchor_target)` (an
  explicit 0 no longer means "almost never"; it means "no preference, I
  carry my share"). The 0/0 floor is 2 at >= 8 requested (was 10) and uses
  the new caps, so a no-nights physician can actually reach 2.
- **Exactly-one-night requests** are exempt from HC-13b (no isolated
  nights): Grishin asked for 1 2400h and got 0 because the rule demanded a
  2nd, penalised night. (The user's note said "1x 0600h"; the data shows
  it was the 2400h -- his 6 requested 0600h were all granted, all on RAH B.)
- **Post-solve RAH A <-> RAH B balance pass** (`scheduler/backend/
  acute_balance.py`, called in `/api/generate` after repair, before
  on-calls): trades same date+time A/B pairs between two physicians when it
  lowers total |A-B|, respecting forbidden_sites, HC-10 adjacent same code,
  max_consecutive_same_site, and never introducing a same-site repeat in a
  run. In that solve ~15 physicians were 0 on one acute side (N Lam 0/4,
  Grishin 0/5, Schindler 4/0, Whiteside 4/0 ...).
- Tests: `test_anchor_fairness.py` (13). N Lam's "NEHC, RAH B, NEHC" run:
  NEHC is now included in the no-repeat-in-a-run penalty at a third of the
  weight (50 vs 150, `_CLUSTER_NEHC_REPEAT_PENALTY`) -- user decision,
  since NEHC has 7 slots/day so some repetition there is unavoidable.
- **Same-day swap bug fixed** (via a sub-agent): there was no backend swap
  at all -- the app composed one from two one-sided `/api/assign` +
  `/api/check-violations` calls, each still seeing the other physician on
  the day. New `POST /api/swap {a, b, dry_run}` unassigns both first, then
  checks/assigns each into the other's slot; frontend swap flow rewired.
  `test_swap.py` (7). Backend restarted locally so it's live.
- **Real-data check of the fairness work** (4-min local solve, 8.7% gap):
  Bacon 60% -> 40% anchors (no unwanted 0600h); Wittmeier/Thirsk/Lucyk/
  Krisik 1 -> 2 anchors; lopsided acute sides 14 -> 2 after 26 A/B trades
  (N Lam 2/2, Grishin 2/3; Brenneis 0/4 and Rawe 3/0 remain -- no legal
  same-slot partner). Two that did NOT move were roster, not solver:
  - Garcea 8/8 0600h is `only_0600h: true` in physicians.yaml -- correct.
    Exposed a flaw: fair-share penalties on an only-one-anchor-type
    physician are dead weight that could starve them; they're now exempt
    (target = their whole request). Test added.
  - Grishin 0 nights: `max_consecutive_nights: 0` in physicians.yaml (from
    the survey's typical_2400h 0) hard-bans nights, overriding his January
    request for 1. Fixed generally: an explicit >=1 night request in the
    month's submission lifts a roster night cap of 0 to 1 (the roster zero
    is a standing assumption; the dated request is the fresher signal).
    Physicians who truly can never work nights should carry
    `forbidden_shift_times: [2400h]` (Francescutti does), which still wins.

**Still to do:**
1. Before the January solve, in the app: open "Previous month (December
   2026)" in the solver card, paste the December master-sheet link, Browse
   to the December requests folder — `/home/cid/Dropbox/KEA Scheduling/December`
   (renamed 2026-10-07 to `<RosterId> - December 2026.xlsx`, 72 files, all
   resolve; originals in `December - pre-rename backup 2026-10-07`; Thirsk
   is PDF-only and one anonymous `.xls` was left untouched) — click Load. Without `google_sheets.yaml`
   the sheet must be link-shared (view) for the public export path to work.
   (Equivalent API: `POST /api/trailing-schedule {"sheet_url", "year":
   2026, "month": 12, "preferences_directory"}`.)
2. Electron UI: `frontend/src/renderer/components/PreviousMonthPanel.jsx`
   (collapsible "Previous month (December 2026)" section inside the CP-SAT
   Solver card, above Generate) takes the master-sheet link + the requests
   folder (Browse… locally, typed path on a remote backend), posts to
   `/api/trailing-schedule`, and shows what loaded: source, counts, who gets
   the acute push, who is discouraged from overage, and the last generate's
   `last_used` note. Inputs are remembered per previous-month in
   localStorage. `electron-vite build` passes; not yet exercised by hand in
   the running app.
3. The two carry-over weights (120 / 150) were chosen by reasoning against
   the existing weight ladder (documented at the constants in
   `generator_cpsat.py`) and verified only in the synthetic contested
   tests — eyeball them on the first real joint solve with December data.
4. Greedy fallback `ScheduleGenerator` ignores both parameters (CP-SAT
   only), same as before.

## Status right now (2026-10-07)

- **KEAsked**: `origin/master` is at `bf8541c` as of last night; 5 more
  commits landed after that from a session on a different machine (see
  below) — HEAD is current with origin as of this writing.
- **sked**: commit `564b7c2` was flagged as unpushed yesterday — check
  `git log origin/main..HEAD` before assuming; may have been pushed since
  (4 more sked commits exist on top of it per yesterday's review, all
  implementing the `preferredCapExempt` feature end-to-end).

## What happened 2026-10-06 (daytime session)

Mostly a run of real-data bug hunts against `cpsat-jan3/4/5.xlsx` test
solves, each one a genuine structural fix, not a tuning tweak:

1. **0/0 anchor-minimum floor** — added earlier, then found broken against
   real data (5/7 eligible physicians still got 0 anchor shifts). Rebuilt as
   its own guaranteed lexicographic stage instead of a soft bonus, same
   treatment the existing anchor-fulfillment guarantee already uses.
2. **Dickey, then a pattern** — his 0/4-nights bug (from an earlier session)
   had the wrong fix (`prefer_singleton_nights`, which fights his actual
   stated preference for back-to-back nights). Replaced with
   `allow_repeat_shift_codes` + `allow_isolated_nights`. Then found the SAME
   underlying contradiction — HC-13b demands a run of ≥2 nights, but some
   other constraint caps a physician below that — recurring for different
   reasons across five more physicians:
   - **Braun**: two real available days 4 days apart, never adjacent →
     `allow_isolated_nights: true` added.
   - **Lung**: explicit "0 2400h" caps him at 1 total, but HC-13b needs ≥2
     if any → floor-eligible physicians now auto-exempted from HC-13b.
   - **Breton, McKinnon, Schindler**: `max_consecutive_nights == 1` makes a
     2+-night run impossible by definition → physicians with that exact cap
     are now auto-exempted from HC-13b too.
   - **McKinnon** also had a separate, unrelated issue: `forbidden_shift_times:
     [0600h, 2400h]` on his roster directly contradicted his own submission
     requesting both — removed.
3. Along the way: seniority weighting (`hire_year`), the Lam/Rico
   combined-submission merge, a `_parse_schedule_xlsx` name-resolution bug
   fix, and a public `/sample` playground + plain-English rulebook shipped
   on sked.keatools.org.

Full backend test suite: 36/36 passing as of `bf8541c`.

## What happened 2026-10-06 night → 2026-10-07 (different machine, reviewed 2026-10-07)

Five more commits landed overnight (`a1f0798` → `7943e03`), reviewed in
detail the morning after. All well-reasoned, each citing a specific real
data case — not speculative tuning:

- **Escalating weekend-overage penalty** (250/500/800/1000 by 1st/2nd/3rd/4th+
  weekend over cap, replacing a flat 250) — fixes a real redistribution bug
  where a flat penalty gave the solver no reason to spread overage instead
  of dumping 5 weekends on one physician (McKinnon) while 15 weekdays sat
  untouched.
- **Universal Fri+Sun-without-Saturday split penalty (150)**, extended from
  `prefer_weekends`-only to everyone — 11.9% of all weekend touches in a
  real solve showed this pattern with nothing discouraging it.
- **Float-shift floor restructured** into front-loaded steps (90 for the
  first float shift, 35 after) so "at least one" actually holds.
- **4-day-run penalty 35→60** — 35 was found to be net *positive* (an
  inversion bug: outweighed by the adjacency bonuses it was meant to
  offset). Exempts `prefer_clustered_nights` (KLam).
- **`avoid_weekday`/`avoid_mondays` solver term removed** — confirmed as a
  no-op (a -5 penalty outweighed by a +6 site tie-break). The roster field
  is now informational only; real "can't work Wednesdays" must come from
  not offering Wednesdays in the submission.
- **Worker count raised to 90% of cores.** ⚠️ Reverses an explicit "don't
  change this yet" caution from earlier in the 2026-10-06 conversation
  (pending a check on crash risk from other Unraid workloads, e.g. Jellyfin
  transcoding competing for cores/memory during a long solve). The new
  commit only cites a utilization measurement, not that safety question —
  worth a deliberate look before trusting it on a long run.
- **Importer's ambiguous-range anchor parsing flipped from high-end to
  low-end** ("6-8" → 6, not 8) + spelled-out number words ("two" → 2). Fixes
  RScheirer's float-shift contradiction (his "6/8" was consuming his entire
  month as nights). This is a roster-wide policy change, not just his case.
- **New `scripts/sync-preferences-to-unraid.sh`** — mirrors a month's real
  xlsx submissions to Unraid (not just YAML config), with real safety
  guards (refuses to mirror an empty folder, dry-run shown first,
  confirmation required). Closes the stale-import gap behind the
  MacGougan/Braun data-mismatch confusion from 2026-10-06.
- **Hard rule**: a 1600h/1800h start now also blocks a 0600h shift two days
  later (previously only 2000h+ starts triggered extended rest) — real case:
  K Smith, 1800h → day off → 0600h, home ~4am then back at 6am.
- **Soft rule**: no repeated site within a run of consecutive days (RAH
  A/B/I/F, not NEHC), plus a penalty for a 2+-day run with zero acute
  shifts anywhere in it.
- **sked integration**: `honor_all_requests` physicians now get
  `preferredCapExempt` pushed through — checked sked's own repo too, this
  is fully implemented end-to-end there (D1 column, server + client
  enforcement, a race-condition fix), nothing left dangling.

**Gap found in review**: none of these 5 commits added or touched a single
test, despite the established pattern (solo-test-against-real-data) from
earlier. Given the volume of new constraint logic, worth going back for —
especially the escalating-step patterns (weekend overage, float floor) and
the new hard post-evening-rest rule.

Full backend test suite re-run 2026-10-07 after pulling this: still
**36/36 passing** (confirms no regression against existing coverage — the
new logic itself has no dedicated tests yet, see above).

## Diagnostic pattern worth knowing

Every one of the 2026-10-06 structural fixes was found with the same
technique: run the physician's **real** submission data solo (zero other
physicians, zero contention) and see if they still get 0. If yes, it's a
structural/hard-constraint contradiction, not a scarcity or weighting
issue — go find which two hard constraints are fighting. See
`scheduler/backend/tests/test_anchor_min_floor.py` and
`test_allow_repeat_shift_codes.py` for the pattern as permanent tests.

## Open items

- **Cross-month continuity**: wired, with UI; needs a first real run against the December sheet — see "Next task" at the top.
- Worker-count bump to 90% of cores — revisit the crash-risk question
  before a long real run (see above).
- No tests added for any of the 5 overnight commits.
- The 0/0 anchor floor's weight (400) was tuned against one synthetic
  contested test, not a full real joint solve with the new guaranteed-stage
  design — worth eyeballing on a real run.
- `McKinnon`'s general anchor-shift issue is resolved, but his
  `rest_after_late_shift` flag is a real, separate constraint on his
  schedule shape — not a bug, just worth remembering if his numbers look
  odd later.

## Where things live

- KEAsked: `/home/cid/Dropbox/KEAclaude/KEAsked` (this repo), synced via Dropbox.
- sked: `/home/cid/Dropbox/KEAclaude/sked`, separate repo, deployed via
  `wrangler deploy` to `sked.keatools.org` (Cloudflare Worker).
- Unraid backend: `root@192.168.0.5`, container `kea-scheduler-backend`,
  repo checkout at `/mnt/user/appdata/kea-scheduler/repo`, config bind-mount
  at `/mnt/user/appdata/kea-scheduler/config`. See
  `.claude/skills/update-unraid-backend/SKILL.md` and
  `.claude/skills/restart-kea-stack/SKILL.md` for the exact procedures.
- Config sync (gitignored real data, separate from git): `./scripts/sync-config-to-unraid.sh`.
- Preferences sync (new 2026-10-06 night): `./scripts/sync-preferences-to-unraid.sh`.

## Known environment quirks

- `git push` and the config-sync script sometimes get blocked by Claude
  Code's own auto-mode safety classifier (reason seen: "Out-of-Place
  Publication" / "Sensitive-Source Provenance") when Claude itself runs
  them — not a real problem with the repo or the command, just that
  classifier being cautious about a private repo it can't verify the
  visibility of. The user running the same command directly has worked
  every time. Don't burn time trying to work around it — just ask the user
  to run it themselves.
- **Never SSH anywhere (Unraid or otherwise) unless explicitly told to for
  that specific action** — this was a direct, explicit correction from the
  user on 2026-10-06 after an unprompted verification pass. Local git/file
  checks are fine; remote actions need to be asked for each time.
