"""
Count "cold" 2400h starts in exported schedule xlsx files: a night run
(first 2400h not preceded by another 2400h) with NO regular shift the day
before. This is what the roster-wide night_ramp_in preference (default on
since 2026-10-28, penalty 150 per cold start in generator_cpsat.py) tries
to reduce. On-call (DOC/NOC) is not a shift here -- the solver can't see
calls either (they're assigned post-solve).

Baseline (January 2027): human schedule 32/68 run starts cold (47%);
solver before the default-on change 35-40/62-66 (55-61%).

    .venv/bin/python3 tools/cold_midnights.py "KEA Scheduling/Tests/January 2027 human.xlsx" Tests/cpsat-janNN.xlsx
"""
import collections
import contextlib
import datetime
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scheduler.api.server import _parse_schedule_xlsx  # noqa: E402
from scheduler.backend.config import load_roster  # noqa: E402


def main(paths: list[str]) -> None:
    roster = load_roster()
    one = datetime.timedelta(days=1)
    for f in paths:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                res = _parse_schedule_xlsx(Path(f), roster)
        except Exception as e:  # noqa: BLE001
            print(f"{Path(f).name}: parse failed: {e}")
            continue
        worked: dict[str, dict[datetime.date, object]] = collections.defaultdict(dict)
        for a in res.assignments:
            worked[a.physician_id][a.date] = a.shift
        nights = starts = cold = 0
        cold_by: collections.Counter = collections.Counter()
        for pid, days in worked.items():
            for d, sh in days.items():
                if sh.time != "2400h":
                    continue
                nights += 1
                prev = days.get(d - one)
                if prev is not None and prev.time == "2400h":
                    continue  # inside a run
                starts += 1
                if prev is None:
                    cold += 1
                    cold_by[pid] += 1
        pct = f"{cold / starts:.0%}" if starts else "n/a"
        multi = {k: v for k, v in cold_by.most_common() if v >= 2}
        print(f"{Path(f).name}: nights={nights} run_starts={starts} cold_starts={cold} ({pct} of starts); "
              f">=2 cold: {multi}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
