# Master sheet: how it is found, and the planned sheet → calendar sync

*Written 2026-10-28, planning stage for the sync; the "how the sheet is
found" part is live code.* Decisions here were made with the user that day.

## How KEAsked (and later, sked) finds a month's sheet — the thing most
## likely to break quietly

Nothing stores a link to any month's spreadsheet. Every lookup is **by
exact file name inside one Drive folder**:

- Folder: `drive_folder_id` in `scheduler/config/google_sheets.yaml`
  (today: the "Test Schedules" folder in the scheduling mailbox's My
  Drive; the real department folder later).
- Name: `file_name_pattern`, default
  `{year}-{month:02d} - {month_name} {year} RAH NECHC Master Schedule`,
  e.g. `2026-12 - December 2026 RAH NECHC Master Schedule` — this is the
  department's real naming, confirmed against the real December 2026 file.
- The Drive query is `'<folder>' in parents and name = '<name>' and
  mimeType = spreadsheet and trashed = false`. First hit wins.

Consequences, i.e. where to look first when something "stops working":

1. **A renamed sheet is invisible.** Even a changed space or a missing
   leading `YYYY-MM`. Symptoms: Push to Master Sheet *creates a second
   file* with the exact name (and the match light says "no sheet" just
   before); Load from master sheet / Previous month say the month is not
   found; the future daily sync skips the month and emails that it looked
   for `<name>` and found nothing. Fix: rename the sheet back, or change
   `file_name_pattern` if the department changed its convention.
2. **A sheet in a different folder is invisible**, including a subfolder.
   Same symptoms. Fix: move it, or point `drive_folder_id` at the right
   folder (the id is the part of the folder URL after `/folders/`).
3. **Two sheets with the same name**: the first one Drive returns is used.
   Trash the stale one.
4. **Access**: in OAuth mode the signed-in account (the scheduling mailbox)
   must be able to edit the folder; for the sync, the service account must
   be able to *view* it. A folder shared with a different account, or a
   sheet whose sharing was tightened, fails with a 403/404 from Google.
5. **Auth modes** (`auth:` in google_sheets.yaml):
   - `oauth` (current): KEAsked acts as the Google account signed in via
     `scripts/google-oauth-login.py`; token in
     `scheduler/config/google_oauth_token.json`, refreshed automatically.
     If the Cloud Console app is ever put back into "Testing", the token
     expires after 7 days and the Push button's tooltip says to sign in
     again. The consent screen's homepage/privacy URLs are
     https://sked.keatools.org/manual and /privacy — those pages exist
     for that reason; don't delete them.
   - `service_account`: cannot *create* files in a My Drive folder
     (Google gives service accounts no storage) — "storage quota
     exceeded" on a push for a month with no sheet yet.
6. **Layout**: names are placed by reading the sheet's own SUN..SAT header
   rows, date rows and shift labels (`sheets_schedule_reader.locate_cells`).
   A sheet with a hand-added row inside a week block still works; a sheet
   whose labels were retyped differently (e.g. "RAH-A") loses that row:
   the push reports "N scheduled cell(s) had no matching row" and the
   match light shows them as "(no row on sheet)".
7. **Nov/Dec/Jan**: the 1st of the next month lives on the previous
   month's sheet (last week row). A January problem on day 1 is a
   December-sheet problem.

## Built 2026-10-28 (night): the official folder, and the daily sync

- The department's official **"Schedules" folder is
  (id kept out of git: `drive_folder_id` in the real `google_sheets.yaml`, and sked's `SHEETS_FOLDER_ID` secret)** (shared "anyone with the link can
  view"). It holds one Drive **shortcut** per month, not the spreadsheets
  themselves (the files are owned by the department's sheet owner, not the scheduling mailbox), so
  `find_month_file_id` now also matches shortcuts and returns the target
  spreadsheet id. Both readers (Python, TS) handle this. KEAsked's
  `drive_folder_id` now points at the official folder too (2026-10-28,
  late): the match light and loads read the department's real sheets.
  The scheduling mailbox only has view access there, so **Push to Master
  Sheet cannot write to it** until the folder is shared with that mailbox
  as Editor -- or until the push is reworked to ask sked to sync.
- **sked Worker sync is live** (sked `src/lib/sheetSync.ts`,
  `sheetReader.ts` = TS port of `sheets_schedule_reader`, `googleAuth.ts`
  = service-account JWT via WebCrypto, `scheduleStore.ts` = the one
  replace-a-period helper shared with `PUT /api/admin/schedule`;
  migration `0012_sheet_sync.sql`: `physician_aliases`,
  `sheet_sync_log`). Cron `0 12 * * *` (05:00/06:00 Edmonton) syncs
  previous/current/next month. Worker vars `SHEETS_FOLDER_ID`,
  `SHEETS_FILE_NAME_PATTERN`, `SYNC_REPORT_TO`, `SYNC_REPORT_FROM`;
  secret `GOOGLE_SERVICE_ACCOUNT_JSON`.
- Verified: the TS reader produced **identical rows** to the Python reader
  for Oct/Nov/Dec 2026 and Jan 2027 from the official folder
  (`sked/scripts/sheet-sync-check.ts` vs `KEAsked/tools/dump_sheet_rows.py`).
  Oct/Nov/Dec/Jan were then synced live (705/701/704/684 rows); January
  replaced KEAsked's generated test month that had been pushed to sked.
- Routes: `PUT /api/admin/physician-aliases` (KEAsked pushes
  `build_alias_index` + display names on every roster save, and sked
  refuses an empty table), `POST /api/admin/sheet-sync {year, month,
  dryRun}`, `GET /api/admin/sheet-sync-log`. KEAsked side:
  `sked_client.push_physician_aliases`, `sked_client.request_sheet_sync`,
  `server._push_physician_aliases`.
- Safety: a sheet that parses to zero slots, an empty alias table, or zero
  resolved names → status `error`, nothing written. Dates a sheet staffs
  are deleted from every other period when written, so the 1st of a month
  (on the previous month's sheet) can never appear twice.
- Email: `src/lib/email.ts` sends through a `send_email` binding when one
  exists; the binding is **not yet in wrangler.jsonc** (Email Routing on
  keatools.org still being enabled by the user). Until then problems are
  only in the sync log / Worker logs.
- Not done yet: Push to Master Sheet still pushes assignments to sked
  itself rather than asking sked to sync (and cannot write to the official
  folder, see above); the `send_email` binding.

## Decisions (2026-10-28): sked reads the sheet daily and republishes (option B)

The **sheet is the source of truth** for what
physicians see. sked's Cloudflare Worker, on a **daily cron**, reads the
previous, current and next month's sheets and replaces each period's
published assignments **and learners**; the physician scheduler sees
nothing of this; learner schedulers only edit the sheet. Errors are
**emailed to the scheduler's address** (sked's `SYNC_REPORT_TO` secret) and kept in a sync log in sked.

- Credentials: the existing service account
  (address in the gitignored `google_service_account.json`),
  shared on the folder as **Viewer**, its key stored as a Worker secret.
  Folder id + name pattern as Worker vars. Same by-name lookup as above,
  so the consequences above apply to the sync too.
- Months: previous (for the spill-over day), current, next. No sheet or
  no sked period → skipped and logged.
- Names → physician ids: KEAsked will push its alias table to sked on
  every roster save (like the rules summary). Unresolved surnames stay on
  the slot with no physician id and are listed in the run log/email.
- Learners: text + fill colour of the row under each shift; type from the
  sheet's legend cells (fallback: the December 2026 colours — blue EM
  resident, red off-service resident, purple elective resident, magenta
  elective student, green core student, yellow shadow student, lavender NP
  student, olive NP); "No learner"/"---"/blank = none; "A/B" = two learners
  of the one colour. Shown on the live page as an italic line with a
  colour dot, and in the .ics event description.
- KEAsked's Push to Master Sheet will write the sheet, then ask sked to
  sync that month immediately (one code path); the match light stays.
- Email: Cloudflare Email Routing on keatools.org with the scheduler's
  address as a verified destination, `send_email` binding from the Worker.
- Order of work: alias push → TypeScript sheet reader with a dry-run
  endpoint (check against December before anything writes) → learner
  display → cron + email.
