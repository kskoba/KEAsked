# KEA Scheduling System — Architecture & Operations Manual

*Document version: 2026-09-29. Supersedes the 2026-03-27 version — that one predated the ByteBloc integration, the sked.keatools.org integration, and most of the current physicians.yaml fields, and its solver weight numbers had drifted from the real code. This version favors describing behavior over hardcoding numbers that will drift again — where an exact weight matters, it points at the constant name in the source rather than a value.*

## Overview

There are **three separate systems** in play, each with its own codebase and its own job:

| System | What it is | Where it runs | Who touches it |
|---|---|---|---|
| **KEAsked** | The scheduling engine itself — imports preferences, validates them, runs the solver, exports a schedule | Electron desktop app (local machine) + a Python FastAPI backend, either spawned locally or run as a container on Unraid | The scheduling coordinator only |
| **sked** (sked.keatools.org) | Physician-facing web tool — magic-link shift-preference grid, "My Rules" view | Cloudflare Worker + D1 database | Physicians (via emailed links) and the coordinator (via KEAsked's admin calls into it) |
| **ByteBloc** | The hospital's actual live scheduling/on-call system | External, not ours | Where the final "unavailable"/"available" signal ends up so ByteBloc's own UI reflects it |

None of these three share a database or a login system. They're bridged entirely by KEAsked making authenticated HTTP calls out to the other two — sked and ByteBloc have no way to reach back into KEAsked on their own initiative.

```
        Excel/xlsx submissions ──┐
                                  │
  sked.keatools.org  ──(pull)──▶ KEAsked  ──(push)──▶ ByteBloc
  (physician grid,               │  Electron + FastAPI
   magic links,        ◀─(push)──┤  scheduler/backend/*.py
   "My Rules")          rules    │
                        summary  ▼
                              generates the schedule
                              (CP-SAT or greedy solver)
```

---

## KEAsked's Own Functions

Everything below is a window in the Electron app, each backed by its own FastAPI endpoints. Every window re-resolves its own backend URL on open (see "Local vs. remote backend" below) — they don't share renderer state with each other.

| Window | Opens from | What it does |
|---|---|---|
| **Setup / Import** | Main screen | Point at a directory of `.xlsx` submissions (or a single flat file), import them for a given year/month |
| **Validate** | After import | Runs `validator.py`'s rule checks per physician, lets you override individual failures or all of a physician's failures at once |
| **Generate** | After validate | Runs the solver (CP-SAT by default, greedy as fallback if OR-Tools isn't installed), shows fill rate / stats, lets you export the result as `.xlsx` |
| **Individual Schedules** (`#schedule-viewer`) | Header icon | Per-physician view of a generated schedule |
| **Send Monthly Shift Requests** (`#monthly-requests`) | Header icon | Pushes a period + master template to sked, generates one magic link per physician, emails them via this app's own SMTP setup (sked never sends email itself). Also supports "load an existing period" to resend/reuse an already-uploaded template, and pulling submissions back **from** sked once physicians have filled them in |
| **Survey Responses** (`#survey-responses`) | Header icon | Viewer for sked's annual-survey responses (the survey mechanism itself is currently not in active use — see "sked" section) |
| **Send Availability to ByteBloc** (`#bytebloc`) | Header icon | Preview and, on typed confirmation, send off/available requests to ByteBloc for the currently-imported period |
| **Physician Roster** (`#roster`) | Header icon | Add/edit/remove physicians in `physicians.yaml` |
| **Scheduling Rules** (`#scheduling-rules`) | Header icon, next to Roster | **Read-only** — shows every person-specific pair/sequencing rule from `scheduler_config.yaml` in plain language. Nothing here is editable; the note in the window says to contact support for changes |
| **Settings** | Header icon | Local vs. remote backend, config directory location |

---

## Local vs. Remote Backend

The Electron app **always** spawns its own local Python backend on startup, regardless of the setting below — that's not optional. "Remote" mode just means the *UI* talks to a different backend instead (typically the one running on Unraid), while the local one still starts up unused in the background.

- Setting lives in `~/.config/kea-scheduler-frontend/kea-settings.json` (`backendMode: "local" | "remote"`, `remoteBackendUrl`).
- **Local mode**: fastest iteration, good for testing config changes, but only this machine sees it.
- **Remote mode**: points at Unraid (`http://<tailscale-ip>:5000` currently), which is what should be used for anything the whole team needs reflected consistently (e.g. actual monthly generation, ByteBloc sends).
- Switching modes requires restarting the Electron app to take effect.

### Getting a code or config change onto Unraid

These are **two separate steps** and both are needed — one without the other leaves Unraid silently running old code against new data, or new code against old data:

1. **Code** (anything in `scheduler/` or `frontend/`): `git push` from here, then on the Unraid box — `git pull`, `docker build`, stop/remove/re-run the container. See the `update-unraid-backend` skill for the exact commands.
2. **Config** (`physicians.yaml`, `scheduler_config.yaml`, `bytebloc.yaml`, `sked.yaml`, `email.yaml` — all gitignored, real org data): `./scripts/sync-config-to-unraid.sh` from this machine. `git push`/`git pull` never touches these files at all.

Neither of these can be run by an AI assistant working in this repo — both require SSH access to the Unraid box, which isn't available here. Always hand the exact commands to a human to run.

---

## The YAML Config Files

All five are gitignored (real org data / secrets) with an annotated `*_template.yaml` (or `.template.yaml`) counterpart committed to git for reference. They live in `scheduler/config/`.

| File | Purpose | Edited via |
|---|---|---|
| **`physicians.yaml`** | One entry per physician: identity, aliases, and every per-person scheduling preference (see next section) | Roster Editor window, or by hand |
| **`scheduler_config.yaml`** | Global solver settings (site-balance targets, spacing rules, default caps) plus **person-specific pair/sequencing rules** — `timed_separation`, `conditional_cowork`, `forbidden_precursor_shifts`, `night_chain_ramp_in`, `linked_rest_pairs` | By hand only — no UI writes to this file. View current rules (read-only) via the Scheduling Rules window |
| **`bytebloc.yaml`** | ByteBloc connection: `group_code`, `location_code`, separate `read_security_token`/`write_security_token`, `requester_id`, `shift_map` (KEAsked shift code → ByteBloc `{site_id, shift_id}`), `provider_map` (KEAsked physician id → ByteBloc provider id) | By hand, seeded from real `getShiftDetails`/`getUserDetails` calls |
| **`sked.yaml`** | sked connection: `base_url`, `survey_base_url`, `admin_secret`, `link_days_valid` | By hand |
| **`email.yaml`** | SMTP settings for the app's own outgoing mail (magic-link emails) | By hand |

Both `physicians.yaml` and `scheduler_config.yaml` are read fresh on every request — **no backend restart needed** after editing either one, on either local or remote. A restart is only needed after a *code* change.

---

## Setting Up a New User

The code (this git repo) and the real data (the five files above) come from **two different places**, and a new person needs both. Cloning the repo alone gets you a working app with no physicians, no ByteBloc connection, and no sked connection — everything gitignored is missing on purpose.

### 1. Get the code

```bash
git clone https://github.com/kskoba/KEAsked.git
# only if they'll also touch the physician-facing site:
git clone https://github.com/kskoba/sked.git
```

Both are **private** repos — they need to be added as a collaborator on GitHub first (or use a token/SSH key that already has access), or the clone will fail.

### 2. Install dependencies

KEAsked:
```bash
cd KEAsked
python3 -m venv .venv
.venv/bin/python3 -c "import fastapi, ortools" 2>&1   # check first -- see below
# if that errors:
.venv/bin/python3 -m ensurepip --upgrade
.venv/bin/pip install -r scheduler/requirements.txt
cd frontend && npm install
```
A freshly-created `.venv` can *look* fine but have no packages in it (some Python builds don't ship `pip` by default) — always run the `import fastapi, ortools` check before assuming it's ready, not just after `venv` creation.

sked (only if needed): `npm install`, plus `npx wrangler login` under a Cloudflare account that has access to this Worker/D1 — needed for `npx wrangler deploy` or any `wrangler d1 execute` command, separate from GitHub access entirely.

### 3. Get the real config files — the part that actually matters here

This whole repo checkout lives inside a Dropbox-synced folder. That has one important consequence:

- **If the new setup is a second computer under the *same* Dropbox account** (e.g. the same person's laptop and desktop), the gitignored config files sync automatically the moment Dropbox finishes syncing the folder — nothing to copy by hand. This is how it's worked for this project so far.
- **If it's a genuinely different person or a machine outside that Dropbox account**, the five real files (`physicians.yaml`, `scheduler_config.yaml`, `bytebloc.yaml`, `sked.yaml`, `email.yaml`) have to be transferred manually into `scheduler/config/` on the new machine. The most current copies live either here or on the Unraid box (`/mnt/user/appdata/kea-scheduler/config/`) — Unraid is arguably the better source of truth day-to-day, since it's the one that gets the `sync-config-to-unraid.sh` pushes. Copy them over a **secure, private channel only** (direct `scp`/`rsync` between machines, not email/Slack/anything that gets logged in plaintext) — they contain live secrets: ByteBloc's write token, sked's admin secret, the email account's app password.
- Only fall back to the committed `*_template.yaml` files if this is truly a from-scratch deployment for a different organization with no existing physician data at all — not the right starting point for "add a colleague to the org's existing setup."

### 4. Point the app at the config, and verify

- Electron's Settings screen has a config-directory picker if the files aren't in the default `scheduler/config/` location.
- Start the app (`run-kea-scheduler` skill has the exact dev-mode command) and confirm `curl http://127.0.0.1:5000/api/health` returns `{"status":"ok"}`, then that the roster actually loads physicians in the Roster Editor — that confirms the real `physicians.yaml` was picked up, not an empty/template one.

### 5. Access the new person will separately need, depending on what they'll do

| Task | Needs |
|---|---|
| Run KEAsked, generate schedules, edit the roster | Just the above |
| Push code changes | Write access to the `kskoba/KEAsked` (and/or `sked`) GitHub repo |
| Rebuild/restart the Unraid container | SSH access to the Unraid box — separate from everything else, ask the existing coordinator |
| Deploy sked or run `wrangler d1 execute` | Cloudflare account access to that Worker/D1, via `wrangler login` |
| Send to ByteBloc | Nothing extra — it's gated by `bytebloc.yaml`'s tokens, already covered in step 3 |

---

## `physicians.yaml` — Per-Physician Fields

The authoritative field list is the `PhysicianConfig` dataclass in `scheduler/backend/config.py` — every field has a comment there explaining exactly what it does and why. Broad categories, as of this writing:

- **Identity**: `id`, `name`, `first_name`/`last_name`, `email`, `aliases` (alternate name spellings a submission file might use — see `physician_resolver.py`), `active`.
- **Hard scheduling limits**: `max_consecutive_shifts` (SIAR), `max_consecutive_nights` (NIAR), `max_weekends`, `forbidden_sites`, `forbidden_shift_times`, `only_2400h`, `only_0600h`, `no_call`, `max_consecutive_same_site`, `max_consecutive_1800h` (no longer independently editable — always tracks `max_consecutive_shifts` now).
- **Soft preferences**: `group_b_site_preference`, `prefer_weekends` (also waives the weekend cap and adds a real steering bonus — see generator_cpsat.py), `honor_all_requests`, `prefer_singleton_nights`, `avoid_mondays`/`avoid_weekday`, `rest_after_late_shift`, `post_block_rest_days`/`post_block_min_length`, `call_linkage`, `anchor_preference`. (`prefer_weekend_clumping` still exists in the schema but is no longer read per-physician — that behavior now applies to everyone.)
- **Administrative / internal** (deliberately excluded from the physician-facing sked summary — see below): `priority_weight`, `combined_headcount`, `default_shifts_requested`, `cap_at_requested`, `casual`, `special_provisions`, `rule_overrides`, `notes`.
- **Annual-survey baseline** (reference only, not solver-enforced): `typical_shifts_per_month`, `typical_0600h_per_month`, `typical_2400h_per_month`.
- **Validation overrides**: `rule_overrides` — replace or disable one of the validator's thresholds for just this physician (see next section).

---

## How to Add a New Physician

1. Roster Editor → "Add Physician" — needs `id` (single word, letters/numbers, must match the prefix physicians' submission filenames will use), first name, last name. Everything else starts at plain defaults.
2. Fill in the rest of their scheduling preferences from the Roster Editor's form.
3. If they'll be sent to ByteBloc: add them to `bytebloc.yaml`'s `provider_map` (their ByteBloc `ProviderId`, found via a `getUserDetails` call) — otherwise `build_shift_requests_payload` will skip them with a warning, silently.
4. If they'll use sked: they need a row in sked's own `physicians` table (id + name + email) before any magic link or rules-summary push will work for them — that's a separate system with its own identity list, not automatically populated from `physicians.yaml`.
5. Save — this auto-pushes their (currently near-empty) rules summary to sked (see below); nothing further needed there.

---

## How to Update Rule Requests

Two very different places, depending on what kind of rule it is:

- **A rule about one physician alone** (their own caps, preferences, restrictions) → `physicians.yaml`, via the **Roster Editor**. Takes effect immediately, no restart.
- **A rule that names two (or more) physicians together** — timed separation, conditional co-working, a linked-rest pair, or a single physician's own day-to-day sequencing restriction that's more specific than a plain field (forbidden precursor shift, night-chain ramp-in) → **`scheduler_config.yaml`**, hand-edited only. There is currently no UI that writes to this file. After editing, view the result (read-only, to confirm it looks right) in the **Scheduling Rules** window. A genuinely *new kind* of rule (not one of the five shapes already supported) needs actual code changes in `generator_cpsat.py`, not just a config edit.

Whichever file you edit, remember the Unraid sync step (`sync-config-to-unraid.sh`) if you want the change to reach anyone using the remote backend — editing the file here only ever affects local runs immediately.

---

## Integration with sked.keatools.org

KEAsked is the admin side; sked has no admin UI of its own beyond what KEAsked's backend calls into via `scheduler/backend/sked_client.py`, authenticated with `sked.yaml`'s `admin_secret` (sent as the `x-admin-secret` header).

**Outbound (KEAsked → sked):**
- `upsert_period` / `upload_period_template` / `generate_period_links` — the "Send Monthly Shift Requests" flow: create/update a period, upload that month's master `.xlsx` template, get back one signed magic-link URL per physician (KEAsked emails these itself; sked never sends mail).
- `push_physician_rules` — pushes a plain-language, per-physician summary of what's actually configured for them (`physicians.yaml` fields + any `scheduler_config.yaml` pair rules naming them — see `config.py`'s `describe_physician_facing_rules` and `server.py`'s `build_sequencing_rule_summaries`) to sked's `physician_rules_summary` table, for sked's "My Rules" view. **Fires automatically on every Roster Editor save** (`update_physician`/`create_physician` in `server.py`) — failures are logged and swallowed, never block the actual save, since this sync is a convenience layer, not the source of truth.

**Inbound (KEAsked ← sked):**
- `list_periods` / `list_period_submissions` / `fetch_physician_export` — the "From Web (sked)" import path: sked reconstructs a physician's filled-preferences `.xlsx` server-side from their stored grid state (`templateFill.ts`, kept in exact parity with `importer.py`'s cell-layout constants) and KEAsked imports it exactly like a locally-uploaded file.
- `list_surveys` / `get_survey_responses` — feeds the Survey Responses viewer.

**What a physician sees on sked itself:**
- The shift-preference grid (mark availability per day/block, Z for "service day", min/max/anchor counts) — this is what gets pulled back into KEAsked as a submission.
- **"My Rules"** — a read-only modal showing the plain-language summary KEAsked pushed for them. This *replaced* an older points/catalogue-based "standing preferences" display tied to an annual self-submitted survey; that survey mechanism still exists in the code (`ruleCatalogue.ts`, `standing_preferences` table, `ruleCatalogue.ts`'s hard/soft points system) but isn't currently referenced anywhere in the live UI. If it's revived (e.g. a June 2027 re-survey), the intended flow is: survey answers get manually reviewed and applied into `physicians.yaml` by the coordinator (same as the September 2026 annual-survey import), which then flows into the "My Rules" push automatically — not a direct survey-to-solver pipeline.

sked's own repo: `/home/cid/Dropbox/KEAclaude/sked` — Cloudflare Worker (`src/index.ts`) + D1 (`sked-db`), deployed with `npx wrangler deploy`. Schema changes are SQL files in `migrations/`, applied with `npx wrangler d1 execute sked-db --remote --file=...`.

---

## Integration with ByteBloc

ByteBloc is the hospital's real scheduling system — the goal is to keep its per-physician off-request state in sync with what's actually in KEAsked, not to schedule from ByteBloc. All of this lives in `scheduler/backend/bytebloc.py`.

**Read-only** (uses `bytebloc.yaml`'s `read_security_token` only): `get_main_schedule`, `get_user_details`, `get_shift_details` — each service has its **own independently-versioned** API version (confirmed with ByteBloc support; not a single shared version), hardcoded as a default in each function.

**Write path** (uses `write_security_token`, and only ever `createShiftRequests`):
- `build_shift_requests_payload` sends one `OffRequest` per mapped shift code, per day, per physician — `OffType: "NeedOff"` when the physician's current submission marks that cell unavailable, `OffType: "Available"` when it's available. This is a **full re-sync of every cell every time**, not an incremental diff — sending `"Available"` explicitly is the only way to clear a previously-sent `NeedOff` on ByteBloc's side (confirmed with ByteBloc support).
- **Delta mode** (optional, on by default in the UI): before building the payload, diff against `bytebloc_last_sent.json` — a local record, **specific to whichever backend instance actually sent last** (local machine or Unraid, not shared between them) — of the off_type last successfully sent for each cell, and skip anything unchanged. Toggle exists in both ByteBloc UI entry points; turning it off forces a full resend regardless of history.
- **Hard safety rule, never to be weakened**: nothing is ever sent without a human typing the literal confirmation phrase `CONFIRM` into the send dialog, checked again server-side. There is no scheduled/automatic call path to `send_shift_requests`.
- `shift_map` and `provider_map` in `bytebloc.yaml` are the only place KEAsked's identities/shift codes get translated to ByteBloc's own IDs — a physician or shift code missing from these mappings is silently skipped from every send (with a warning, not an error) — always double check both maps when something's missing from a send that should be there.

There are currently **two UI entry points** for the same ByteBloc send feature (`ByteBlocPanel.jsx`'s own window and a button inside `ValidationPanel.jsx`) — a known duplication, left in place deliberately rather than consolidated mid-testing.

---

## The Solver (`generator_cpsat.py`, CP-SAT — the one actually used; `generator.py`, greedy, is the fallback if OR-Tools isn't installed and is otherwise dead code for this purpose)

Hard constraints are grouped and commented in the source with `HC-N` labels — the numbering has grown non-sequentially as rules were added (`HC-9a`/`9b`/`9c`/`9d`, `HC-11` appears twice for historical reasons, `HC-12`/`12b`/`12c`, `HC-13`/`13b`/`13c`) so treat it as a set of labeled sections to search for, not a strict 1-to-N list. Broad categories:

- **Structural**: one physician per slot, one shift per physician per day, availability, forbidden sites/times, `only_2400h`/`only_0600h`.
- **Rest & spacing**: 23h minimum between shift starts (hard), soft penalties for a >36h gap or a >10h same-to-next-day time "swing", max consecutive working days, max consecutive nights, no isolated 2400h (unless `prefer_singleton_nights`), no night/day-off/night pattern.
- **Anchor shifts**: combined 0600h+2400h cap, relative to what the physician actually requested.
- **Weekends**: proportional cap (scaled to requested shift count) unless `prefer_weekends` waives it entirely; `prefer_weekends` also adds a real steering bonus toward weekend assignment and a penalty for a fragmented Fri+Sun-without-Saturday pattern.
- **Person-specific pair/sequencing rules**, all driven by `scheduler_config.yaml` (see "How to Update Rule Requests" above): `linked_rest_pairs`, `conditional_cowork`, `timed_separation` (checked same-day *and* adjacent-day, both directions), `forbidden_precursor_shifts`, `night_chain_ramp_in` (soft).
- **Site balance**: Group A (RAH A/B, acute) vs. Group B (NEHC/RAH I/RAH F, non-acute) target ratio, alternation reward, same-site-repeat penalty.

Exact weight numbers change often (there's a real tuning history in the source comments — several have been raised, lowered, and raised again against real monthly data). Don't hardcode them into a second document; read the `_CONSTANT_NAME = N` definition and its surrounding comment directly in `generator_cpsat.py` for the current value and why it's set that way.

---

## Component Glossary

| Component | Path | Role |
|---|---|---|
| Importer | `scheduler/backend/importer.py` | Parse `.xlsx` submissions into `PhysicianSubmission` objects (fixed cell layout — kept in exact parity with sked's `templateFill.ts` and `public/app.js`) |
| Validator | `scheduler/backend/validator.py` | Submission-quality checks (valid days/blocks/weekend days/anchored days), overridable per-physician via `rule_overrides` |
| CP-SAT solver | `scheduler/backend/generator_cpsat.py` | The real solver — OR-Tools constraint programming |
| Greedy solver | `scheduler/backend/generator.py` | Fallback only if OR-Tools is unavailable; also still used for `repair_pass`/`assign_on_calls` helpers |
| Config loader | `scheduler/backend/config.py` | `physicians.yaml` parsing, `PhysicianConfig`, `describe_physician_facing_rules` |
| Physician identity resolution | `scheduler/backend/physician_resolver.py` | Maps any name/filename variant to a canonical roster id — exact-match only, never fuzzy |
| ByteBloc client | `scheduler/backend/bytebloc.py` | Everything in the "Integration with ByteBloc" section above |
| sked client | `scheduler/backend/sked_client.py` | Everything in the "Integration with sked" section above |
| FastAPI server | `scheduler/api/server.py` | HTTP layer tying all of the above together |
| React frontend | `frontend/src/renderer/` | One component per window listed in "KEAsked's Own Functions" above |
