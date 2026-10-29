import logging, math, datetime
logging.disable(logging.CRITICAL)
from pathlib import Path
from collections import defaultdict, Counter
import openpyxl
from scheduler.api import server
from scheduler.backend import sheets_schedule_reader as r
from scheduler.backend.config import load_roster
from scheduler.backend.importer import import_directory

YEAR, MONTH = 2027, 1
T = Path("/home/cid/Dropbox/KEA Scheduling/Tests")
roster = load_roster(); cfg = server._load_scheduler_config()
subs = import_directory(Path("/home/cid/Dropbox/KEA Scheduling/January"), YEAR, MONTH)
server._apply_roster(subs, roster); server._apply_combined_submissions(subs, roster); server._apply_shift_count_overrides(subs, roster)
sub_by = {s.physician_id: s for s in subs}
_orig = r.resolve_physician_id
r.resolve_physician_id = lambda n, i: _orig(n, i) or f"?{n}"

def load(path):
    if "human" in path.name.lower():
        ws = openpyxl.load_workbook(path, data_only=True, read_only=True).worksheets[0]
        grid = [list(x) for x in ws.iter_rows(min_row=1, max_row=400, max_col=9, values_only=True)]
        res = r.parse_schedule_grid(grid, YEAR, MONTH, roster)
        res.unfilled = [u for u in res.unfilled if u.date.day != 1]
        res.assignments = [a for a in res.assignments if a.date.day != 1]
        return res
    return server._parse_schedule_xlsx(path, roster)

def we_key(d):
    wd = d.weekday()
    return d - datetime.timedelta(days=wd - 4) if wd in (4, 5, 6) else None

def clusters_of(al):
    c = defaultdict(dict)
    for a in al:
        k = we_key(a.date)
        if k: c[k][a.date.weekday()] = a
    return c

v2 = load(T/"cpsatv2-jan7.xlsx"); hu = load(T/"January 2027 human.xlsx")
by_v2 = defaultdict(list); by_hu = defaultdict(list)
for a in v2.assignments: by_v2[a.physician_id].append(a)
for a in hu.assignments: by_hu[a.physician_id].append(a)

print("Weekend slot supply (both schedules fill the same 630 slots):")
we_slots = Counter(a.date.strftime("%a") for a in v2.assignments if a.date.weekday() in (4,5,6))
print("  ", dict(we_slots), "total", sum(we_slots.values()), "of", len(v2.assignments))

def shape(cl):
    days = sorted(cl)
    return "".join({4:"F",5:"S",6:"U"}[d] for d in days)

print("\n=== Fri+Sun-without-Sat splits ===")
for label, by in (("jan7", by_v2), ("human", by_hu)):
    print(f"-- {label}")
    for pid, al in sorted(by.items()):
        for k, cl in clusters_of(al).items():
            if 4 in cl and 6 in cl and 5 not in cl:
                s = sub_by.get(pid); c = roster.get(pid)
                # was Saturday offered that weekend?
                sat = k + datetime.timedelta(days=1)
                sat_day = next((d for d in s.days if d.date == sat), None) if s else None
                sat_offered = bool(sat_day and sat_day.wants_to_work)
                print(f"   {pid:12} wk of Fri {k.day:2}: Fri {cl[4].shift.code:18} Sun {cl[6].shift.code:18}  Sat offered={sat_offered}  prefer_weekends={bool(c and c.prefer_weekends)}")

print("\n=== How weekend clusters are shaped (count of physician-weekends by days worked) ===")
for label, by in (("jan7", by_v2), ("human", by_hu)):
    shapes = Counter(); per_phys = Counter()
    for pid, al in by.items():
        for k, cl in clusters_of(al).items():
            shapes[shape(cl)] += 1; per_phys[pid] += 1
    print(f"-- {label}: shapes {dict(sorted(shapes.items(), key=lambda kv: -kv[1]))}")
    print(f"   physician-weekends total {sum(shapes.values())}; avg weekend shifts per physician-weekend {sum(we_slots.values())/sum(shapes.values()):.2f}")
    print(f"   physicians touching N weekends: {dict(sorted(Counter(per_phys.values()).items()))}")

print("\n=== Per-physician weekend load, where the two differ most ===")
rows = []
for pid in set(by_v2) | set(by_hu):
    a = clusters_of(by_v2.get(pid, [])); b = clusters_of(by_hu.get(pid, []))
    wa = sum(len(c) for c in a.values()); wb = sum(len(c) for c in b.values())
    rows.append((pid, len(a), wa, len(b), wb, len(by_v2.get(pid, [])), len(by_hu.get(pid, []))))
print(f"{'pid':13}{'v2 wkends':>10}{'v2 we-days':>11}{'hu wkends':>10}{'hu we-days':>11}{'v2 shifts':>10}{'hu shifts':>10}")
for row in sorted(rows, key=lambda x: -(abs(x[1]-x[3]) + abs(x[2]-x[4]))):
    if abs(row[1]-row[3]) >= 2 or abs(row[2]-row[4]) >= 3:
        print(f"{row[0]:13}{row[1]:10}{row[2]:11}{row[3]:10}{row[4]:11}{row[5]:10}{row[6]:10}")

print("\n=== Lone Fridays (F / FU clusters): is the Friday the tail of a weekday run? ===")
for label, by in (("jan7", by_v2), ("human", by_hu)):
    attached = lone = 0; thu_worked = 0
    for pid, al in by.items():
        days = {a.date for a in al}
        for k, cl in clusters_of(al).items():
            if 4 in cl and 5 not in cl:
                lone += 1
                if (k - datetime.timedelta(days=1)) in days: thu_worked += 1
    print(f"-- {label}: Friday-without-Saturday clusters {lone}; of those, Thursday also worked {thu_worked}")

print("\n=== prefer_weekends physicians: weekend days / shifts ===")
pref = [p for p, c in roster.items() if c.prefer_weekends]
for label, by in (("jan7", by_v2), ("human", by_hu)):
    tot_we = tot = 0
    parts = []
    for p in pref:
        al = by.get(p, [])
        if not al: continue
        we = sum(1 for a in al if a.date.weekday() in (4,5,6)); tot_we += we; tot += len(al)
        parts.append(f"{p} {we}/{len(al)}")
    print(f"-- {label}: {tot_we}/{tot} weekend days overall; {', '.join(parts)}")

print("\n=== Weekend days per physician (non-prefer) in v2 vs human: who carries the extra weekends? ===")
diff = []
for pid in set(by_v2) | set(by_hu):
    if pid in pref: continue
    a = sum(1 for x in by_v2.get(pid, []) if x.date.weekday() in (4,5,6)); b = sum(1 for x in by_hu.get(pid, []) if x.date.weekday() in (4,5,6))
    diff.append((a-b, pid, a, b, len(by_v2.get(pid,[])), len(by_hu.get(pid,[]))))
diff.sort()
print("  v2 gives MORE weekend days than human:", [(p, f"{a} vs {b}") for d,p,a,b,_,_ in diff if d >= 2])
print("  v2 gives FEWER weekend days than human:", [(p, f"{a} vs {b}") for d,p,a,b,_,_ in diff if d <= -2])
