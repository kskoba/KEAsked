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

## Learner Schedule Creation and Viewing

Not started.

---

## View a physician's submitted monthly preferences (button → opens their sked magic link)

**Status**: backend + frontend built and wired end-to-end, 2026-09-23. **Not yet committed/pushed** (sitting as local changes in this checkout) — do that first if picking this up elsewhere. **Not yet visually verified** — no click-simulation tool was available in the session that built this, so the actual rendered UI in the Roster Editor window has not been looked at by a human yet. Everything *except* that has been checked.

**What it does**: in the Physician Roster editor, select a physician, then in a new "Preference sheet" section (right after "Preferences", before "Validation rule overrides") pick an existing sked period from a dropdown and click "View in browser" — opens that physician's sked magic link (their real, already-saved shift-preference grid for that period) in the system's default browser. Read-only from the app's side — never emails anything, unlike the existing "Send Monthly Shift Requests" / survey-resend actions; sked just re-signs a fresh token against their already-stored submission each time, so it's safe to click repeatedly.

**Files touched**:
- `scheduler/backend/sked_client.py` — generalized `list_surveys()` into `list_periods(config, kind=None)` (kind: `"shift_request"` | `"survey"` | `None` for both); `list_surveys` is now a thin wrapper over it for backward compat.
- `scheduler/api/schemas.py` — new `PeriodInfo`, `PeriodsResponse`, `PhysicianLinkRequest`, `PhysicianLinkResponse`.
- `scheduler/api/server.py` — two new endpoints, both live-tested against the real deployed sked (`sked.keatools.org`), not just imported/compiled:
  - `GET /api/sked/periods?kind=shift_request` — list of real existing periods `[{id, label, opens_at, closes_at}]`. **Confirmed working**: returned `test-period`, `2026-12`, `2027-01` (×2, one stray duplicate-looking id `2027-01-01` — probably worth a quick look, not investigated), `2026-11`. Note **October 2026 is not in this list** — that month's real physician submissions predate sked's existence (built 2026-09-22) and came in via the old individual-xlsx-file process, not through sked at all. This feature will only work for periods actually sent out *through* sked going forward.
  - `POST /api/sked/physician-link` (`{physician_id, period_id}` → `{url}`) — generates a link via the same `sked_client.generate_period_links()` call the existing send/resend flows use, just returns the URL instead of emailing it. **Confirmed working**: tested live with `{"physician_id":"RScheirer","period_id":"test-period"}`, got back a real signed `https://sked.keatools.org/?token=...` URL.
- `frontend/src/main/index.js` — new `shell:openExternal` IPC handler (didn't exist before this — no prior "open URL in browser" capability anywhere in the app). Deliberately restricted to `http(s)://` only (throws otherwise), so a renderer bug or bad backend response can never hand the OS a `file://` or custom-protocol string.
- `frontend/src/preload/index.js` — exposes it as `window.electronAPI.openExternal(url)`.
- `frontend/src/renderer/api.js` — `getSkedPeriods(kind)`, `getPhysicianLink(physicianId, periodId)`.
- `frontend/src/renderer/RosterEditor.jsx` — the new "Preference sheet" section: period list loaded once on mount (roster-wide, not per-physician, so it's separate from the `form` state), a `SelectField` + button, busy/error states. Full `npm run build` (production, via electron-vite/esbuild) succeeds clean with this in place.

**What's verified**: backend imports cleanly, all 3 new/changed backend files compile, the 2 new routes are registered (`GET /api/sked/periods`, `POST /api/sked/physician-link`), both hit live and returned real data from the actual Cloudflare-deployed sked site. Frontend: full production build succeeds, main app window launches and renders without crashing post-change.

**What's NOT verified** (do this first when resuming):
1. Actually open the Roster Editor window in the running app, select a physician, confirm the "Preference sheet" section renders correctly and the dropdown is populated.
2. Click "View in browser" for a real physician + the `test-period` id (safe — it's clearly a test period, not a real submission window) and confirm a browser tab actually opens with a working sked page.
3. Check the `2027-01` vs `2027-01-01` duplicate-looking period id noticed above — may be nothing, may be a stray test artifact from earlier session work, may indicate an id-format inconsistency worth understanding before relying on the picker.
4. Decide whether this should also offer `kind=survey` periods (annual survey) from the same UI, or if that's intentionally left to the existing separate Survey Responses viewer (leaning toward "leave separate," but wasn't a deliberate decision, more a default).

**To commit when ready** (from `~/Dropbox/KEAclaude/KEAsked`, or wherever this checkout lives on the other computer):
```bash
git add scheduler/backend/sked_client.py scheduler/api/schemas.py scheduler/api/server.py \
        frontend/src/main/index.js frontend/src/preload/index.js \
        frontend/src/renderer/api.js frontend/src/renderer/RosterEditor.jsx docs/feature-roadmap.md
git commit -m "feat: view a physician's sked preference sheet from the Roster Editor"
git push origin master
```
Then the Unraid box needs its usual `git pull` + rebuild (see the `update-unraid-backend` skill) if the remote backend will be used to test this.

## on the individual schedule tab in app, when viewing someone's finalized schedule underneath it should show: requested number of shifts, max shifts, number of nights, number of 0600h and any specific rules for that person. also the individual schedules should show actually what shift they have on that day, not just the time 

