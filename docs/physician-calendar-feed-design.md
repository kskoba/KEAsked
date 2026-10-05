# Physician calendar subscription feed (.ics)

## Context

Physicians currently only see their schedule by opening the Electron app's
Individual Schedules viewer or reading an exported xlsx — there's no way for
a physician to have their assigned shifts show up automatically in their own
Google/Apple/Outlook calendar. The ask: let each physician subscribe once to
a personal calendar feed URL, so their calendar always reflects their current
shift schedule without them having to check the app or re-download anything.

The right mechanism for "subscribe" (as opposed to a one-time export) is a
standard iCalendar (`.ics`) feed URL — the same "subscribe by URL" mechanism
every major calendar client already supports, requiring no per-physician
OAuth. The one real limitation, inherent to that mechanism and not something
this design can work around: **calendar apps control their own refresh
interval** (Google Calendar has historically refreshed a subscribed URL
roughly every 8–24 hours, not instantly). This is near-real-time, not
instant — worth being upfront about with physicians when this ships.

Two repos are involved: **KEAsked** (Electron+FastAPI, generates the
schedule) and **sked** (Cloudflare Worker+D1, already public at
sked.keatools.org, already has a magic-link token scheme). sked is the
natural host for the public feed endpoint; KEAsked's job is to push the
generated schedule to it.

## Architecture

**sked currently has no concept of an actual generated schedule** — only
physician-submitted preferences/availability (`submissions`,
`standing_preferences`, `survey_responses`). A new table is needed to store
real assignment output, distinct from those.

### 1. sked: new D1 table + admin push endpoint

New migration `sked/migrations/0008_schedule.sql`:

```sql
CREATE TABLE schedule_assignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  period_id TEXT NOT NULL REFERENCES periods(id),
  physician_id TEXT NOT NULL REFERENCES physicians(id),
  date TEXT NOT NULL,          -- ISO yyyy-mm-dd, America/Edmonton calendar date
  kind TEXT NOT NULL CHECK (kind IN ('shift','on_call')),
  shift_time TEXT,             -- e.g. '0600h' (kind='shift' only)
  shift_site TEXT,             -- e.g. 'RAH A side' (kind='shift' only)
  call_type TEXT,              -- 'DOC'|'NOC' (kind='on_call' only)
  is_manual INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_schedule_period ON schedule_assignments(period_id);
CREATE INDEX idx_schedule_physician_date ON schedule_assignments(physician_id, date);

ALTER TABLE periods ADD COLUMN schedule_published_at TEXT;
```

`period_id` reuses the existing `periods` table (KEAsked already uses
`"YYYY-MM"` period ids elsewhere).

**Upsert semantics: full replace per publish, not per-row merge.** A
`ScheduleResult` is always a complete recomputation for the month, so on
every publish: delete all `schedule_assignments` rows for that `period_id`
and re-insert the full new set, in one D1 `.batch()` call (atomic, trivial
row count — roughly 15–20 physicians × ~30 days).

New admin route in `sked/src/index.ts`, alongside the existing admin routes
(same `requireAdmin` pattern):

```
PUT /api/admin/schedule
  body: { periodId, assignments: [{physicianId, date, kind, shiftTime?, shiftSite?, callType?, isManual?}, ...] }
```

Also one more small admin route to mint a calendar-feed link:
`POST /api/admin/calendar-link` — `{physicianId}` → `{url}`.

### 2. Public `.ics` feed endpoint

**Token**: reuse `sked/src/lib/token.ts`'s `signToken`/`verifyToken` exactly
as-is. A calendar-feed token is *not* period-scoped (must keep working across
every future month) — mint it with a sentinel `periodId` value like
`"__calendar__"` that the feed route never actually looks up (it queries
`schedule_assignments` by `physicianId` only, so the sentinel is
documentation, not a live check).

**Expiry must be a fixed constant, not `now + offset`** — deliberately
different from `generate-period-links`' `exp = now + daysValid*86400`
pattern. That pattern makes every mint call produce a different valid token
for the same physician, which is fine for a one-off emailed link but wrong
here: the calendar link needs to be the *same* URL every time it's displayed
(Roster Editor re-opening a physician, a re-sent reminder email, etc.), with
nothing stored anywhere to make that consistent. Since `signToken` is a pure
HMAC over its payload, using a fixed far-future expiry constant (e.g. a
literal year-2099 timestamp, not `Date.now() + N`) makes minting fully
deterministic — the same `physicianId` always signs to the exact same token
string, forever. That's what makes "no persistence anywhere" (see below)
actually work.

**No persistence — mint on demand, every time.** Unlike the schedule data
itself, the calendar link needs no database row, no cache, on either side.
`POST /api/sked/calendar-link` (KEAsked) → `POST /api/admin/calendar-link`
(sked) just signs and returns the token each call; because it's
deterministic, repeated calls for the same physician always return the
identical URL. This is what lets it be displayed for copy/paste in Roster
Editor without KEAsked or sked needing to remember anything — just call the
mint endpoint fresh whenever it needs to be shown.

**Route**: `GET /api/calendar/<token>.ics` — verify token, query
`schedule_assignments` for that `physicianId` across all periods, render to
ICS text, return with `content-type: text/calendar; charset=utf-8`. No new
npm dependency — ICS is simple enough to hand-build as plain string
concatenation in a new `sked/src/lib/ics.ts`, the same way KEAsked hand-builds
its xlsx export.

**Stable, deterministic UID scheme** — critical for correct updates on
republish:

```
UID = `${physicianId}-${date}-${kind}-${discriminant}@sked.keatools.org`
// discriminant = shiftTime for kind='shift' (e.g. "0600h")
//              = callType  for kind='on_call' (e.g. "DOC")
```

Because a republish deletes+reinserts the full period, a changed shift gets a
new UID and the old one simply stops appearing — a well-behaved ICS client
diffs by UID on each refresh (add what's new, remove what's missing). No
explicit `STATUS:CANCELLED`/SEQUENCE bookkeeping needed for v1 — see risk
note below.

**Timezone**: hardcode a static `VTIMEZONE` block for `America/Edmonton`
(standard Olson boilerplate) and emit every `DTSTART`/`DTEND` as local
wall-clock time with `TZID=America/Edmonton`. Simpler than UTC conversion —
"0600h" always means 06:00 local regardless of DST, so no DST arithmetic
needed server-side at all; the calendar client's own VTIMEZONE handling does
that.

**Shift duration / DTEND — already resolved, contrary to first pass.** The
Plan agent that helped design this flagged shift duration as an unresolved
blocker (nothing in `shifts.py`/`Shifts.md` records shift length, only start
time). That's true of those files specifically, but the real data already
exists: `KEAsked/scheduler/api/server.py`'s `_EXPORT_SHIFTS` table (used by
the xlsx export) pairs every `(site, time)` combination with a full
`"HHMM-HHMM"` range — e.g. `0600h RAH A/B` → `0600-1200` (6h) but
`0600h NEHC/RAH I` → `0600-1400` (8h); duration depends on site, not just
time. **Action item**: extract this into a clean
`SHIFT_TIME_RANGES: dict[tuple[str,str], str]` (keyed by `(time, site)`) in
`shifts.py`, where shift domain knowledge actually belongs, and have
`_EXPORT_SHIFTS` and the new calendar-push code both consume it — don't
leave the ICS feature as the second copy of this data. DOC/NOC durations are
also already in `_EXPORT_SHIFTS` (`0500-1559`, `1600-0459`).

**On-call display**: distinguish from regular shifts in `SUMMARY` —
`On Call (Day)` / `On Call (Night)` vs. `{shiftSite} — {shiftTime}`. Note
`is_manual` in `DESCRIPTION` when true.

### 3. KEAsked-side changes

New functions in `KEAsked/scheduler/backend/sked_client.py` (same style as
existing `upsert_period`/`generate_period_links`):
- `push_schedule(config, period_id, assignments)` — PUT to the new sked route.
- `get_calendar_link(config, physician_id)` — POST to mint a token, returns URL.

Payload conversion (`ScheduleResult` → wire format) belongs in `server.py`
near the new route, not in `sked_client.py` (keep that a thin transport
layer):

```python
def _result_to_schedule_payload(result: ScheduleResult) -> list[dict]:
    out = [
        {"physicianId": a.physician_id, "date": a.date.isoformat(), "kind": "shift",
         "shiftTime": a.shift.time, "shiftSite": a.shift.site, "isManual": a.is_manual}
        for a in result.assignments
    ]
    out += [
        {"physicianId": c.physician_id, "date": c.date.isoformat(), "kind": "on_call", "callType": c.call_type}
        for c in result.on_calls
    ]
    return out
```

New routes in `KEAsked/scheduler/api/server.py`, alongside the existing
sked-integration block:
- `POST /api/sked/publish-schedule` — pushes `_state["result"]` to sked.
- `POST /api/sked/calendar-link` — mints one physician's link.

New request/response models in `scheduler/api/schemas.py`, next to
`PhysicianLinkRequest`/`PhysicianLinkResponse`.

**UI placement**:
- **Calendar link, in `RosterEditor.jsx`** — a new section per physician
  (separate from the existing "Preference sheet" section, which is
  period-scoped *input* and a different concept from this permanent,
  physician-scoped link) showing the link in a read-only field with a
  **Copy** button, plus an **Email Calendar Link** button. This is the
  scheduler's actual management surface for physicians, and since minting is
  deterministic and stateless (see above), the field can just fetch fresh
  from `/api/sked/calendar-link` whenever that physician's record is opened
  — no caching needed. The "Email Calendar Link" button calls
  `scheduler/backend/email_sender.py`'s existing `send_email()` (same SMTP
  config already used for monthly-request reminders) with a new one-time
  template explaining what the link is and how to subscribe in
  Google/Apple/Outlook — no new email infrastructure needed, just a new
  subject/body and the button wiring.
- **"Get Calendar Link"** could optionally also appear in
  `PhysicianScheduleViewer.jsx` next to the physician's name header (the
  existing per-physician *output* view) as a secondary convenience, but
  RosterEditor is the primary, decided placement.
- **"Publish Schedule to Calendars"** (month-level) → next to wherever the
  existing xlsx "Export" action lives, since it operates on the same
  `_state["result"]`.

**Explicit publish, not automatic-on-generate.** A schedule is regenerated
many times per cycle while exploring alternatives before being finalized
(generate → review → manual tweaks → maybe regenerate). Auto-publishing every
intermediate draft would put still-changing schedules on physicians' phones,
and — given the refresh-latency limitation above — a stale/wrong copy could
sit uncorrected for up to a day. Matches the existing precedent in this
codebase: "Send Monthly Shift Requests" and other physician-facing pushes are
already explicit-click-only, never automatic. A separate "Publish" button
also gives a natural place to show "last published: {schedule_published_at}"
so the scheduler can see if physicians' calendars are currently stale
relative to the in-memory draft.

## Staged rollout

**Slice 0 — read-only proof, no UI, no KEAsked changes.** Hand-build one
`PUT /api/admin/schedule` payload via curl for one real physician + a
handful of real dates, then actually subscribe to the resulting
`GET /api/calendar/<token>.ics` URL in a real Google/Apple calendar. This is
the only way to confirm the two things nothing else can verify:
whether the VTIMEZONE block renders correctly across a DST boundary, and
whether the calendar client's subscription diffing genuinely
adds/removes-by-UID as assumed. Files: `sked/migrations/0008_schedule.sql`,
`sked/src/index.ts`, `sked/src/lib/ics.ts`.

**Slice 1 — KEAsked wiring, manual end-to-end test.** Add the two
`sked_client.py` functions, the two `server.py` routes, one button each in
`PhysicianScheduleViewer.jsx`. Test with one real already-generated month.
No bulk actions yet.

**Slice 2 — deferred until 0/1 are proven correct.**
- Bulk "get all calendar links" (e.g. folded into the existing monthly-email
  flow) — don't build this before Slice 0/1 are verified; a wrong UID scheme
  or duration reaching every physician's phone at once via one bulk email is
  the exact failure mode to avoid.
- Rolling date-window trimming on the feed query (e.g. only fetch
  `date >= today - 30 days`) for feed-size hygiene once months accumulate.
- Extract `SHIFT_TIME_RANGES` into `shifts.py` (see above) before Slice 1's
  DTEND logic ships for real, not after.
- Token revocation/rotation — not needed for v1 (a physician leaving is rare
  enough that manually re-minting or rotating `HMAC_SECRET` is an adequate
  blunt fallback); don't build a revocation table speculatively.

## Risk / what could go wrong

The riskiest part isn't anything server-side — it's the unverifiable
assumption about how calendar clients actually treat a subscribed feed on
refresh. The delete-and-reinsert design relies on "the client diffs the full
feed by UID and removes what's missing," true for well-behaved consumers but
a client implementation detail sked can't control or unit-test — only
confirm by literally watching a real subscription (Slice 0, not something to
build UI on top of speculatively). If some client doesn't reliably remove
stale events (some Outlook versions are known to lag here), the fix isn't a
redesign — it's emitting an explicit `STATUS:CANCELLED` VEVENT for any UID
present before a publish and absent after, which needs tracking prior state
instead of blind delete+reinsert. Worth deferring that complexity until
Slice 0 actually shows it's needed.

## Verification

1. Slice 0: subscribe a real calendar app to a hand-seeded feed; confirm
   events appear at correct local times, and that changing/removing a row
   and re-publishing correctly updates/removes the corresponding event after
   the client's next refresh.
2. Slice 1: generate a real month in the Electron app, click "Publish", click
   "Get Calendar Link" for one physician, subscribe, confirm it matches the
   Individual Schedules viewer exactly (including on-calls).
3. Confirm re-publishing after a manual roster-editor override correctly
   updates that one physician's calendar without duplicating or losing other
   events.

---

## Status as of 2026-09-30 — Slice 0 built and live, paused here

**Slice 0 is done and deployed to production sked**, tested against a
synthetic physician (not a real person). Picking this back up needs no
re-derivation of the design — everything below is real, current state.

**What's actually live:**
- `sked/migrations/0008_schedule.sql` — applied to remote D1. Note:
  applying it surfaced that migration `0007_physician_rules_summary.sql` had
  been run by hand at some point and was never recorded in wrangler's
  `d1_migrations` tracking table, which blocked `wrangler d1 migrations
  apply` entirely. Fixed by manually inserting the missing tracking row —
  if a future migration mysteriously fails to apply, check
  `d1_migrations` for gaps like this first.
- `sked/src/lib/ics.ts` — builds the `.ics` feed. Two corrections already
  applied after real review, both confirmed and deployed:
  - A `2400h` shift dated D starts at **23:59 of D itself** (not 00:00 of
    D+1) — deliberate, for safety/clarity, so the event visibly anchors to
    the date the roster attributes it to rather than appearing to belong to
    the next day in a calendar view. Ends at the real end time on D+1.
  - **Every regular (non-call) shift is 8h, regardless of site** — corrected
    from an earlier draft that copied KEAsked's `_EXPORT_SHIFTS` xlsx-display
    table verbatim (which had a 6h/8h split by site). `REGULAR_SHIFT_START_HOURS`
    + `regularShiftRange()` compute end time as `(start + 8) % 24` uniformly.
    DOC/NOC keep their own real spans (`0500-1559` / `1600-0459`) — this
    duration change is display-only for the calendar feed and does not
    affect any KEAsked scheduling rule (CP-SAT/rest-spacing logic only ever
    compares shift *start* times, never duration).
- Three routes live in `sked/src/index.ts`: `PUT /api/admin/schedule`
  (full replace per period, atomic `env.DB.batch()`), `POST
  /api/admin/calendar-link` (deterministic mint — fixed far-future expiry
  constant `CALENDAR_TOKEN_EXP`, not `now + offset`, so repeated calls for
  the same physician always return the identical URL, no storage needed),
  `GET /api/calendar/<token>.ics` (public feed, token-authed).
- **New, beyond the original Slice 0 scope** — a companion "check right now"
  live-view page, added because Google Calendar's subscription refresh lag
  (observed firsthand: a fix didn't visibly show up for several minutes,
  prompted "is there another option with better refresh times") made clear
  physicians need a way to see the true current state on demand, not just
  wait on a poll cycle:
  - `GET /api/schedule-view?token=...` (JSON, reuses the same calendar
    token via the existing `requireToken` helper — same link works for
    both the `.ics` subscription and this view).
  - `sked/public/my-schedule.html` + `my-schedule.css` + `my-schedule.js` —
    a small standalone page, token read from `?token=` query param same as
    the existing shift-request/survey pages, with a manual Refresh button.

**Test data currently sitting in production D1** (harmless — isolated
from all real data, but should be deleted once you're done poking at it):
- `physicians` row: `id='calendar-test'`
- `periods` row: `id='calendar-test-period'`
- `schedule_assignments` rows for `calendar-test` (4 rows, Oct 31–Nov 2 2026,
  deliberately spanning the Nov 1 2026 DST fall-back transition)
- Cleanup: `DELETE FROM schedule_assignments WHERE physician_id =
  'calendar-test'; DELETE FROM periods WHERE id = 'calendar-test-period';
  DELETE FROM physicians WHERE id = 'calendar-test';` via `wrangler d1
  execute sked-db --remote --command "..."`

**Live test link** (still valid until you delete the test physician above):
`https://sked.keatools.org/api/calendar/Y2FsZW5kYXItdGVzdA.X19jYWxlbmRhcl9f.4070908800.bbns8gUD9i9oL6D9q54EJaJiVyiUFyRPxxwcU93Ed-A.ics`
and the matching live view:
`https://sked.keatools.org/my-schedule.html?token=Y2FsZW5kYXItdGVzdA.X19jYWxlbmRhcl9f.4070908800.bbns8gUD9i9oL6D9q54EJaJiVyiUFyRPxxwcU93Ed-A`

**Not yet done from the original Slice 0/1 scope:**
- You haven't yet confirmed the real-calendar-app subscribe test end to
  end (rendering was verified server-side/via API only — the actual "watch
  a real Google/Apple calendar handle this" step from the Slice 0
  definition is still open).
- Slice 1 (KEAsked-side wiring: `sked_client.py` functions, `server.py`
  routes, Roster Editor buttons) hasn't been started at all — Slice 0 was
  done via hand-built curl calls, deliberately, per the plan.

---

## Phase 2 — Master Google Calendar + learner pairing (new scope, 2026-09-30)

This reframes part of the original plan. Context that changed it: the
department's actual source of truth today is a set of **real Google
Calendars in a shared folder**, hand-maintained, viewable by the whole
group — not something this design accounted for originally (which only
ever considered *individual* per-physician feeds). Those calendars also
carry **learner (resident/student) pairings** — which learner is working
with which physician on a given shift — entered by a separate person who
does learner scheduling, and that process stays fully manual; KEAsked must
never write to the learner-scheduling sheet, only read from it.

**Confirmed decisions so far:**
- Keep both: the master calendar(s) for the whole group's shared view, AND
  the individual per-physician sked link/live-view for personal use — not
  either/or.
- Learner pairing data entry stays 100% manual, on the existing sheet.
  KEAsked's role is read-only there.
- **Still undecided, explicitly discussed as "not sure yet / needs
  discussion" and not resolved**: whether KEAsked should write directly
  into the SAME real Google Calendars the group already uses today, or
  into new/separate calendars first as a safer proving-out period before
  cutting over. Recommendation on the table (not yet agreed): start
  separate, given the blast radius of a bug reaching the group's actual
  live staffing calendar is much higher than anything tested so far (which
  has all been a synthetic `calendar-test` physician nobody depends on).
  **Resolve this explicitly before writing to any real calendar.**

**Real file examined and understood** — `~/Downloads/2026-09 - September
2026 RAH NECHC Master Schedule.xlsx` (this month's real master schedule).
Its layout is the *same* shift-name-row / time-code-row pairing KEAsked's
own xlsx export uses (see `_EXPORT_SHIFTS` in `server.py`) — strongly
suggesting this file starts life as a KEAsked export, and the
learner-scheduling person then fills in the per-day cells of what's
normally the blank time-code row with learner names instead of leaving
them empty:

```
RAH A          Sachs      Wittmeier   Lali        Davis    Rosenblum
0600-1200  →   Wang       Brooke H.   Compagna    —        —          (learner row; "No Learner" when none)
```

So a learner is positionally addressable exactly like a physician: same
row-pair walk, one row lower. Shift types that never carry a learner (calls,
some others observed in the real file) consistently show `"No Learner"`
rather than blank — a safe, unambiguous sentinel to check for.

**Recommended architecture** (not yet built):
1. KEAsked reads the learner-scheduling Google Sheet (Sheets API,
   read-only) for the target month whenever the learner scheduler's pass is
   done for that month — walks shift-row/learner-row pairs exactly like the
   analysis above, producing `{date, site, time, learner_name}`.
2. Resolves the physician name in each shift row to a `physician_id` using
   the *same* roster-alias-matching logic KEAsked already relies on
   elsewhere (`canon_name`/roster key map) — don't build a second name
   resolver.
3. Folds `learner_name` into the payload already pushed to sked's
   `schedule_assignments` via `PUT /api/admin/schedule` — needs one new
   column, `learner_name TEXT`, added via a new migration
   (`sked/migrations/0009_...sql`).
4. The **same** publish action also pushes the combined physician+learner
   event data to the real master Google Calendar (Calendar API), using one
   shared, already-existing Google account (the one that currently sends
   KEAsked's emails) — a single one-time authorization, not per-physician
   OAuth, which is what makes this tractable (per-physician OAuth was
   already ruled out earlier in this doc as too heavy).
5. **Deliberately no new integration needed on sked's side for the
   learner data** — since `learner_name` is just another stored column by
   the time sked serves it, both `ics.ts` (add it to `SUMMARY`/`DESCRIPTION`)
   and `my-schedule.js` (render it inline) pick it up automatically. sked
   stays a dumb storage/serving layer; all Google API integration work
   belongs in KEAsked, alongside the existing ByteBloc/email integrations
   (new module, e.g. `scheduler/backend/google_calendar_client.py` /
   `google_sheets_client.py`, same style as `sked_client.py`/`bytebloc.py`).
6. Workflow ordering this implies: "Publish" is not necessarily a single
   one-time action per month — the scheduler would click it once when the
   physician schedule is ready (learner names come back `"No Learner"`/empty
   at that point, since the sheet isn't filled in yet), then click it again
   once the learner scheduler's pass is done. Full-replace-per-publish
   already handles this correctly with no new mechanism needed.

**Blocking prerequisite — needs the user to do this, not something to
build around:**
1. In Google Cloud Console, on whatever project the shared emailing Google
   account belongs to (or a new project under that account), enable the
   **Google Calendar API** and **Google Sheets API**.
2. Create a **service account**, generate its JSON key.
3. Share the target master Calendar(s) with that service account's email
   as **Editor**.
4. Share the learner-scheduling Google Sheet with that service account's
   email as **Viewer only** — KEAsked must never get write access there.
5. Hand the JSON key over so it can be wired in the same way
   `bytebloc.yaml`/`sked.yaml` work today: a gitignored real config file
   under `scheduler/config/`, with a committed `_template.yaml` counterpart.

**Where to resume next session:**
1. Confirm same-vs-separate master calendar decision (still open, see
   above) before anything else in this phase.
2. Once the Google Cloud credential setup (blocking prerequisite above) is
   done, build the KEAsked-side reader/writer modules and the
   `learner_name` migration on sked.
3. Independently of Phase 2, Slice 0's real-calendar subscribe test is
   still open — worth finishing that verification regardless of when
   Phase 2 starts, since it validates the individual-feed mechanism Phase 2
   builds on top of.
