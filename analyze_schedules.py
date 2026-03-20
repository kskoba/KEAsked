import sys
sys.stdout.reconfigure(encoding='utf-8')

import openpyxl
from collections import defaultdict
from datetime import date, timedelta

TIME_MAP = {
    '0600-1200': '0600h',
    '0600-1400': '0600h',
    '0900-1700': '0900h',
    '1000-1800': '1000h',
    '1200-1800': '1200h',
    '1200-2000': '1200h',
    '1400-2200': '1400h',
    '1500-2300': '1500h',
    '1600-0459': '1600h',
    '1600-2400': '1600h',
    '1700-0100': '1700h',
    '  1800-0000': '1800h',
    '1800-0000': '1800h',
    '1800-0200': '1800h',
    '2000-0400': '2000h',
    '2400-0600': '2400h',
    '2400-0800': '2400h',
}

SITE_MAP = {
    'RAH A': 'RAH A side',
    'RAH B': 'RAH B side',
    'NECHC': 'NEHC',
    'NECHC ': 'NEHC',
    'RAH I': 'RAH I side',
    'RAH Float': 'RAH F side',
}

SKIP_SITES = {'AM CALL', 'PM CALL'}

DAY_COLS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT']


def parse_schedule(filepath):
    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))

    assignments = []  # (physician, date_obj, shift_code)

    i = 0
    while i < len(rows):
        row = rows[i]

        # Detect header row: col B (index 1) == 'SUN'
        if row[1] == 'SUN':
            # Next row has date numbers
            if i + 1 >= len(rows):
                i += 1
                continue
            date_row = rows[i + 1]
            # Cols B-H (indices 1-7) have date numbers
            # We need to figure out the month/year from context
            # Dates are just day numbers; we'll collect them and resolve later
            day_numbers = []
            for col_idx in range(1, 8):  # B through H
                val = date_row[col_idx]
                if val is not None:
                    try:
                        day_numbers.append(int(val))
                    except (ValueError, TypeError):
                        day_numbers.append(None)
                else:
                    day_numbers.append(None)

            # Now read site/time pairs
            j = i + 2
            current_week_data = []  # list of (site, time_code, col_idx 1-7, physician)

            while j < len(rows):
                site_row = rows[j]
                # Check if this is another header row
                if site_row[1] == 'SUN':
                    break
                # Check if site_row col A has a site label
                site_label = str(site_row[0]).strip() if site_row[0] is not None else ''
                if site_label == '' or site_label == 'None':
                    j += 1
                    continue

                # Could be a site row - check if next row has a time
                if j + 1 < len(rows):
                    time_row = rows[j + 1]
                    # Check col A of time row for a time string
                    time_val = str(time_row[0]).strip() if time_row[0] is not None else ''

                    # Is it a time range?
                    is_time = time_val in TIME_MAP or time_val.strip() in TIME_MAP
                    if not is_time:
                        # Maybe col A is None and time is in col B?
                        # Try checking if site_label looks like a time
                        j += 1
                        continue

                    if site_label in SKIP_SITES:
                        j += 2
                        continue

                    site_code = SITE_MAP.get(site_label)
                    if site_code is None:
                        j += 2
                        continue

                    time_key = time_val if time_val in TIME_MAP else time_val.strip()
                    time_code = TIME_MAP.get(time_key, time_key)
                    shift_code = f"{time_code} {site_code}"

                    # Extract physician names from site_row cols B-H (indices 1-7)
                    for col_idx in range(1, 8):
                        physician = site_row[col_idx]
                        if physician is not None:
                            physician = str(physician).strip()
                            if physician and physician != 'None' and physician != '':
                                current_week_data.append((col_idx - 1, physician, shift_code))
                                # col_idx-1 maps to day_numbers index (0=SUN...6=SAT)

                    j += 2
                else:
                    j += 1

            # Now we need to resolve day numbers to actual dates
            # We'll store them with day_numbers and resolve in a second pass
            for (day_offset, physician, shift_code) in current_week_data:
                day_num = day_numbers[day_offset] if day_offset < len(day_numbers) else None
                if day_num is not None:
                    assignments.append((physician, day_num, shift_code, i))  # i = week_row_index for grouping

            i = j
        else:
            i += 1

    return assignments, rows


def resolve_dates(assignments_raw, year=2026, month=6):
    """Convert day numbers to actual date objects. Handle month transitions."""
    resolved = []
    for (physician, day_num, shift_code, week_idx) in assignments_raw:
        if day_num is None:
            continue
        # June 2026: days 1-30 are June, if day_num > 28 could be late month, if day_num < 5 could be July
        # We need to handle the week that spans month boundary
        # Strategy: track month based on day number sequence
        # For June 2026 schedule, most days are June (1-30)
        # Days <= 30 are June, days that appear after day 28 but are small (1-7) are July
        # We'll handle this by checking context - for now use simple heuristic
        try:
            d = date(year, month, int(day_num))
            resolved.append((physician, d, shift_code))
        except ValueError:
            # Day out of range - might be July
            try:
                d = date(year, month + 1, int(day_num))
                resolved.append((physician, d, shift_code))
            except ValueError:
                pass
    return resolved


def parse_file(filepath):
    """Full parse returning list of (physician, date, shift_code)."""
    print(f"\nParsing: {filepath}")
    assignments_raw, rows = parse_schedule(filepath)
    resolved = resolve_dates(assignments_raw)
    print(f"  Raw assignments found: {len(assignments_raw)}")
    print(f"  Resolved assignments: {len(resolved)}")
    return resolved


# ---- ALTERNATIVE PARSER: more robust row-by-row ----

def parse_file_v2(filepath, year=2026, month=6):
    """Robust parser that handles the schedule format carefully."""
    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb.active

    rows = []
    for row in ws.iter_rows(values_only=True):
        rows.append(row)

    assignments = []  # (physician, date_obj, shift_code)

    i = 0
    current_dates = {}  # col_idx (0-6) -> date_obj

    while i < len(rows):
        row = rows[i]

        # Ensure row has enough columns
        row = list(row) + [None] * 10  # pad

        col_a = str(row[0]).strip() if row[0] is not None else ''
        col_b = str(row[1]).strip() if row[1] is not None else ''

        # Header row detection: col B == 'SUN'
        if col_b == 'SUN':
            # Next row: date numbers
            i += 1
            if i < len(rows):
                date_row = list(rows[i]) + [None] * 10
                current_dates = {}
                for col_idx in range(7):  # 0=SUN ... 6=SAT
                    val = date_row[col_idx + 1]  # cols B-H
                    if val is not None:
                        try:
                            day_num = int(val)
                            # Resolve to actual date
                            # Heuristic: if day_num > 25 and we're tracking a month boundary
                            try:
                                d = date(year, month, day_num)
                            except ValueError:
                                try:
                                    d = date(year, month + 1, day_num) if month < 12 else date(year + 1, 1, day_num)
                                except ValueError:
                                    d = None
                            current_dates[col_idx] = d
                        except (ValueError, TypeError):
                            pass
            i += 1
            continue

        # Site row detection: col A has a known site label
        site_label = col_a
        if site_label in SITE_MAP or site_label in SKIP_SITES:
            # Next row should be time row
            if i + 1 < len(rows):
                time_row = list(rows[i + 1]) + [None] * 10
                time_val_raw = str(time_row[0]).strip() if time_row[0] is not None else ''

                if site_label in SKIP_SITES:
                    i += 2
                    continue

                site_code = SITE_MAP.get(site_label, site_label)
                time_code = TIME_MAP.get(time_val_raw) or TIME_MAP.get('  ' + time_val_raw)
                if time_code is None:
                    # Try stripping
                    stripped = time_val_raw.strip()
                    time_code = TIME_MAP.get(stripped)

                if time_code and current_dates:
                    shift_code = f"{time_code} {site_code}"
                    # Extract physicians from cols B-H (indices 1-7)
                    for col_idx in range(7):
                        physician = row[col_idx + 1]
                        if physician is not None:
                            physician = str(physician).strip()
                            if physician and physician.lower() not in ('none', ''):
                                d = current_dates.get(col_idx)
                                if d:
                                    assignments.append((physician, d, shift_code))
                i += 2
                continue

        i += 1

    return assignments


# ---- MAIN ----

file_machine = r"C:\Users\kskob\Dropbox\KEAclaude\KEAsked\Request-Imports\test-schedule.xlsx"
file_human   = r"C:\Users\kskob\Dropbox\KEAclaude\KEAsked\Request-Imports\June26-complete.xlsx"

print("=" * 70)
print("PARSING MACHINE-GENERATED SCHEDULE")
print("=" * 70)
machine_assignments = parse_file_v2(file_machine)
print(f"Total assignments parsed: {len(machine_assignments)}")

print("\n" + "=" * 70)
print("PARSING HUMAN-GENERATED SCHEDULE")
print("=" * 70)
human_assignments = parse_file_v2(file_human)
print(f"Total assignments parsed: {len(human_assignments)}")

# Show sample from each
print("\n--- Sample machine assignments (first 10) ---")
for a in sorted(machine_assignments, key=lambda x: x[1])[:10]:
    print(f"  {a[0]:20s} {a[1]} {a[2]}")

print("\n--- Sample human assignments (first 10) ---")
for a in sorted(human_assignments, key=lambda x: x[1])[:10]:
    print(f"  {a[0]:20s} {a[1]} {a[2]}")

# Build lookup structures
def build_physician_schedule(assignments):
    """Returns dict: physician -> list of (date, shift_code)"""
    sched = defaultdict(list)
    for (phys, d, sc) in assignments:
        sched[phys].append((d, sc))
    for phys in sched:
        sched[phys].sort(key=lambda x: x[0])
    return sched

machine_sched = build_physician_schedule(machine_assignments)
human_sched   = build_physician_schedule(human_assignments)

# ============================================================
print("\n" + "=" * 70)
print("ANALYSIS 1: Same shift on consecutive days (machine schedule)")
print("=" * 70)

violations = []
for phys, shifts in machine_sched.items():
    for idx in range(len(shifts) - 1):
        d1, sc1 = shifts[idx]
        d2, sc2 = shifts[idx + 1]
        if sc1 == sc2 and (d2 - d1).days == 1:
            violations.append((phys, d1, sc1, d2))

if violations:
    print(f"Found {len(violations)} consecutive-day same-shift violations:")
    for (phys, d1, sc, d2) in sorted(violations):
        print(f"  {phys:20s}  {d1} -> {d2}  [{sc}]")
else:
    print("No consecutive-day same-shift violations found.")

# ============================================================
print("\n" + "=" * 70)
print("ANALYSIS 2: Singleton 2400h shifts (machine schedule)")
print("=" * 70)

singletons_2400 = []
for phys, shifts in machine_sched.items():
    # Get all dates with 2400h
    night_dates = sorted([d for (d, sc) in shifts if '2400h' in sc])
    for idx, nd in enumerate(night_dates):
        prev_date = night_dates[idx - 1] if idx > 0 else None
        next_date = night_dates[idx + 1] if idx < len(night_dates) - 1 else None
        has_prev = prev_date is not None and (nd - prev_date).days == 1
        has_next = next_date is not None and (next_date - nd).days == 1
        if not has_prev and not has_next:
            singletons_2400.append((phys, nd))

print(f"Singleton 2400h shifts: {len(singletons_2400)}")
for (phys, nd) in sorted(singletons_2400):
    print(f"  {phys:20s}  {nd}")

# ============================================================
print("\n" + "=" * 70)
print("ANALYSIS 3: Fill rate comparison")
print("=" * 70)

EXPECTED_TOTAL = 630  # 21 shifts/day × 30 days

# Count filled shifts (non-empty physician assignments)
machine_filled = len(machine_assignments)
human_filled   = len(human_assignments)

# Count unique (date, shift_code) slots filled
machine_slots = set((d, sc) for (p, d, sc) in machine_assignments)
human_slots   = set((d, sc) for (p, d, sc) in human_assignments)

print(f"Machine schedule:")
print(f"  Total physician assignments: {machine_filled}")
print(f"  Unique (date, shift_code) slots filled: {len(machine_slots)}")
print(f"  Expected total slots: {EXPECTED_TOTAL}")
print(f"  Apparent unfilled slots: {EXPECTED_TOTAL - len(machine_slots)} (based on expected)")

print(f"\nHuman schedule:")
print(f"  Total physician assignments: {human_filled}")
print(f"  Unique (date, shift_code) slots filled: {len(human_slots)}")
print(f"  Apparent unfilled slots: {EXPECTED_TOTAL - len(human_slots)}")

# ============================================================
print("\n" + "=" * 70)
print("ANALYSIS 4: Per-physician shift counts")
print("=" * 70)

all_physicians = sorted(set(list(machine_sched.keys()) + list(human_sched.keys())))

print(f"{'Physician':<25} {'Machine':>8} {'Human':>8} {'Diff':>8}")
print("-" * 55)
for phys in all_physicians:
    mc = len(machine_sched.get(phys, []))
    hc = len(human_sched.get(phys, []))
    diff = mc - hc
    flag = " <-- LARGE DIFF" if abs(diff) >= 3 else ""
    print(f"  {phys:<23} {mc:>8} {hc:>8} {diff:>+8}{flag}")

# ============================================================
print("\n" + "=" * 70)
print("ANALYSIS 5: Physicians in human schedule not in machine schedule")
print("=" * 70)

human_only = [p for p in human_sched if p not in machine_sched or len(machine_sched[p]) == 0]
machine_only = [p for p in machine_sched if p not in human_sched or len(human_sched[p]) == 0]
few_in_machine = [p for p in human_sched if p in machine_sched and 0 < len(machine_sched[p]) <= 2 and len(human_sched[p]) >= 3]

print("In human but NOT in machine schedule:")
for p in sorted(human_only):
    print(f"  {p} (human: {len(human_sched[p])} shifts)")

print("\nIn machine but NOT in human schedule:")
for p in sorted(machine_only):
    print(f"  {p} (machine: {len(machine_sched[p])} shifts)")

print("\nIn human with >= 3 shifts, but very few (<=2) in machine:")
for p in sorted(few_in_machine):
    print(f"  {p} (machine: {len(machine_sched[p])}, human: {len(human_sched[p])})")

# ============================================================
print("\n" + "=" * 70)
print("ANALYSIS 6: Shift time distribution (machine vs human)")
print("=" * 70)

TIME_SLOTS = ['0600h', '0900h', '1000h', '1200h', '1400h', '1500h', '1600h', '1700h', '1800h', '2000h', '2400h']

machine_time_counts = defaultdict(int)
human_time_counts   = defaultdict(int)

for (p, d, sc) in machine_assignments:
    for ts in TIME_SLOTS:
        if sc.startswith(ts):
            machine_time_counts[ts] += 1

for (p, d, sc) in human_assignments:
    for ts in TIME_SLOTS:
        if sc.startswith(ts):
            human_time_counts[ts] += 1

print(f"{'Time Slot':<12} {'Machine':>8} {'Human':>8} {'Diff':>8}")
print("-" * 42)
for ts in TIME_SLOTS:
    mc = machine_time_counts[ts]
    hc = human_time_counts[ts]
    diff = mc - hc
    print(f"  {ts:<10} {mc:>8} {hc:>8} {diff:>+8}")

# Also show site distribution
print()
SITES = ['RAH A side', 'RAH B side', 'NEHC', 'RAH I side', 'RAH F side']
machine_site_counts = defaultdict(int)
human_site_counts   = defaultdict(int)

for (p, d, sc) in machine_assignments:
    for site in SITES:
        if site in sc:
            machine_site_counts[site] += 1

for (p, d, sc) in human_assignments:
    for site in SITES:
        if site in sc:
            human_site_counts[site] += 1

print(f"{'Site':<15} {'Machine':>8} {'Human':>8} {'Diff':>8}")
print("-" * 45)
for site in SITES:
    mc = machine_site_counts[site]
    hc = human_site_counts[site]
    diff = mc - hc
    print(f"  {site:<13} {mc:>8} {hc:>8} {diff:>+8}")

# ============================================================
print("\n" + "=" * 70)
print("ANALYSIS 7: 2400h run analysis")
print("=" * 70)

def get_runs(night_dates):
    """Returns list of runs (each run is a list of consecutive dates)."""
    if not night_dates:
        return []
    runs = []
    current_run = [night_dates[0]]
    for nd in night_dates[1:]:
        if (nd - current_run[-1]).days == 1:
            current_run.append(nd)
        else:
            runs.append(current_run)
            current_run = [nd]
    runs.append(current_run)
    return runs

print("\n--- Machine schedule 2400h runs ---")
for phys in sorted(machine_sched.keys()):
    night_dates = sorted([d for (d, sc) in machine_sched[phys] if '2400h' in sc])
    if not night_dates:
        continue
    runs = get_runs(night_dates)
    run_desc = ', '.join(
        f"{r[0].strftime('%b%d')}" if len(r) == 1 else f"{r[0].strftime('%b%d')}-{r[-1].strftime('%b%d')}({len(r)}d)"
        for r in runs
    )
    print(f"  {phys:<22} total={len(night_dates):2d}  runs: {run_desc}")

print("\n--- Human schedule 2400h runs ---")
for phys in sorted(human_sched.keys()):
    night_dates = sorted([d for (d, sc) in human_sched[phys] if '2400h' in sc])
    if not night_dates:
        continue
    runs = get_runs(night_dates)
    run_desc = ', '.join(
        f"{r[0].strftime('%b%d')}" if len(r) == 1 else f"{r[0].strftime('%b%d')}-{r[-1].strftime('%b%d')}({len(r)}d)"
        for r in runs
    )
    print(f"  {phys:<22} total={len(night_dates):2d}  runs: {run_desc}")

# ============================================================
print("\n" + "=" * 70)
print("ANALYSIS 8: Rule deviations and anomalies")
print("=" * 70)

# 8a: Physicians working multiple shifts on same day
print("\n8a. Physicians with multiple shifts on same day (machine):")
multi_day = []
for phys, shifts in machine_sched.items():
    by_date = defaultdict(list)
    for (d, sc) in shifts:
        by_date[d].append(sc)
    for d, scs in by_date.items():
        if len(scs) > 1:
            multi_day.append((phys, d, scs))
if multi_day:
    for (phys, d, scs) in sorted(multi_day):
        print(f"  {phys:20s} {d}: {scs}")
else:
    print("  None found.")

# 8b: Long runs of consecutive work days (any shift)
print("\n8b. Physicians with 4+ consecutive work days (machine):")
for phys, shifts in sorted(machine_sched.items()):
    work_dates = sorted(set(d for (d, sc) in shifts))
    if len(work_dates) < 4:
        continue
    runs = []
    current = [work_dates[0]]
    for d in work_dates[1:]:
        if (d - current[-1]).days == 1:
            current.append(d)
        else:
            runs.append(current)
            current = [d]
    runs.append(current)
    long_runs = [r for r in runs if len(r) >= 4]
    if long_runs:
        for r in long_runs:
            print(f"  {phys:20s} {r[0]} to {r[-1]} ({len(r)} days)")

# 8c: Consecutive days comparison - how many in machine vs human
machine_consec = sum(1 for phys, shifts in machine_sched.items()
                     for idx in range(len(shifts)-1)
                     if (shifts[idx+1][0] - shifts[idx][0]).days == 1)
human_consec   = sum(1 for phys, shifts in human_sched.items()
                     for idx in range(len(shifts)-1)
                     if (shifts[idx+1][0] - shifts[idx][0]).days == 1)

print(f"\n8c. Total consecutive-day pairs: Machine={machine_consec}, Human={human_consec}")

# 8d: Shift variety per physician
print("\n8d. Physicians assigned many different shift types (machine):")
for phys in sorted(machine_sched.keys()):
    shift_types = set(sc for (d, sc) in machine_sched[phys])
    if len(shift_types) > 3:
        print(f"  {phys:20s} {len(shift_types)} distinct shift types: {sorted(shift_types)}")

# 8e: Days with 0 coverage in machine
print("\n8e. Shift slots with NO physician assigned (machine):")
# Get all shift_codes in human schedule to know what slots should exist
all_shift_codes = set(sc for (p, d, sc) in human_assignments)
all_dates_machine = set(d for (p, d, sc) in machine_assignments)
machine_slot_set = set((d, sc) for (p, d, sc) in machine_assignments)

uncovered = []
for d in sorted(all_dates_machine):
    for sc in sorted(all_shift_codes):
        if (d, sc) not in machine_slot_set:
            uncovered.append((d, sc))

if uncovered:
    print(f"  {len(uncovered)} unfilled (date, shift_code) pairs in machine schedule:")
    # Group by shift code
    by_sc = defaultdict(list)
    for (d, sc) in uncovered:
        by_sc[sc].append(d)
    for sc in sorted(by_sc.keys()):
        dates = sorted(by_sc[sc])
        print(f"    {sc}: {len(dates)} days missing -- {[str(d) for d in dates[:5]]}{'...' if len(dates)>5 else ''}")
else:
    print("  No completely unfilled slots detected.")

# 8f: Overall summary statistics
print("\n8f. Summary statistics:")
print(f"  Machine: {len(machine_sched)} physicians, {len(machine_assignments)} total assignments")
print(f"  Human:   {len(human_sched)} physicians, {len(human_assignments)} total assignments")

# Physicians shared between both
shared = set(machine_sched.keys()) & set(human_sched.keys())
print(f"  Physicians in both: {len(shared)}")
print(f"  Only in machine: {sorted(set(machine_sched.keys()) - set(human_sched.keys()))}")
print(f"  Only in human: {sorted(set(human_sched.keys()) - set(machine_sched.keys()))}")

print("\n" + "=" * 70)
print("ANALYSIS COMPLETE")
print("=" * 70)
