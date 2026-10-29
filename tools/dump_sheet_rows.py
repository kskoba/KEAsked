"""
Dump what the Python master-sheet reader produces for a month, plus the
roster's alias index, so sked's TypeScript port can be checked against it
(sked/scripts/sheet-sync-check.ts). Writes aliases.json and <yyyy-mm>.json
into the given directory. Uses the real folder passed as --folder (defaults
to google_sheets.yaml's).

  .venv/bin/python tools/dump_sheet_rows.py OUT_DIR 2026 12 [--folder FOLDER_ID]
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scheduler.api.server import _result_to_sked_assignments  # noqa: E402
from scheduler.backend import google_sheets_client, sheets_schedule_reader  # noqa: E402
from scheduler.backend.config import load_roster  # noqa: E402
from scheduler.backend.physician_resolver import build_alias_index, build_display_names  # noqa: E402


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out_dir, year, month = Path(args[0]), int(args[1]), int(args[2])
    folder = sys.argv[sys.argv.index("--folder") + 1] if "--folder" in sys.argv else None
    out_dir.mkdir(parents=True, exist_ok=True)

    roster = load_roster()
    aliases = build_alias_index(roster)
    names = build_display_names(roster)
    (out_dir / "aliases.json").write_text(json.dumps({
        "aliases": aliases,
        "physicians": [{"id": pid, "name": names.get(pid, pid)} for pid in sorted(roster)],
    }, indent=1))

    config = google_sheets_client.load_google_sheets_config()
    if folder:
        config = dataclasses.replace(config, drive_folder_id=folder)
    file_id = google_sheets_client.find_month_file_id(config, year, month)
    if not file_id:
        sys.exit(f"no sheet for {year}-{month:02d}")
    values, fills = google_sheets_client.read_grid_with_fills(config, file_id, "A1:I600")
    unresolved: list[str] = []
    result = sheets_schedule_reader.parse_schedule_grid(values, year, month, roster, unresolved=unresolved)
    learners = sheets_schedule_reader.extract_learners(values, fills, year, month)
    rows = _result_to_sked_assignments(result, learners=learners, include_spillover=True)
    for r in rows:
        r.pop("isManual", None)
    (out_dir / f"{year}-{month:02d}.json").write_text(json.dumps(rows, indent=0))
    print(f"{year}-{month:02d}: file {file_id}, {len(rows)} rows, {sum(1 for r in rows if r.get('learners'))} with learners, unresolved {unresolved}, {len(aliases)} aliases")


if __name__ == "__main__":
    main()
