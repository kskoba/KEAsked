import sys, logging, math, datetime, statistics
logging.disable(logging.CRITICAL)
from pathlib import Path
from collections import defaultdict, Counter
import openpyxl
from scheduler.api import server
from scheduler.backend import acute_balance, sheets_schedule_reader as r
from scheduler.backend.config import load_roster
from scheduler.backend.importer import import_directory

YEAR, MONTH = 2027, 1
T = Path("/home/cid/Dropbox/KEA Scheduling/Tests")
roster = load_roster(); cfg = server._load_scheduler_config()
subs = import_directory(Path("/home/cid/Dropbox/KEA Scheduling/January"), YEAR, MONTH)
server._apply_roster(subs, roster); server._apply_combined_submissions(subs, roster); server._apply_shift_count_overrides(subs, roster)
sub_by = {s.physician_id: s for s in subs}
share = cfg["anchor_shifts"].get("anchor_share_target", 0.40); tol = cfg["anchor_shifts"].get("anchor_target_tolerance", 1)
max_we = cfg["weekends"]["max_weekends_per_month"]; ft = cfg["weekends"]["full_time_shifts_per_month"]

unres = Counter()
_orig = r.resolve_physician_id
def lenient(name, idx):
    pid = _orig(name, idx)
    if pid is None: unres[name] += 1; return f"?{name}"
    return pid
r.resolve_physician_id = lenient

def load(path):
    if "human" in path.name.lower():
        ws = openpyxl.load_workbook(path, data_only=True, read_only=True).worksheets[0]
        grid = [list(x) for x in ws.iter_rows(min_row=1, max_row=400, max_col=9, values_only=True)]
        res = r.parse_schedule_grid(grid, YEAR, MONTH, roster)
        # Compare Jan 2-31 only: the solver files were run from Jan 2 (Jan 1 is
        # December's), so drop the human sheet's blank Jan 1 column.
        res.unfilled = [u for u in res.unfilled if u.date.day != 1]
        res.assignments = [a for a in res.assignments if a.date.day != 1]
        res.on_calls = [o for o in res.on_calls if o.date.day != 1]
        return res
    return server._parse_schedule_xlsx(path, roster)

def we_key(d):
    wd = d.weekday()
    return d - datetime.timedelta(days=wd - 4) if wd in (4, 5, 6) else None

def metrics(res):
    m = {}
    by = defaultdict(list)
    for a in res.assignments: by[a.physician_id].append(a)
    m["filled"] = len(res.assignments); m["unfilled"] = len(res.unfilled); m["physicians"] = len(by)
    # totals vs requested
    under = over = 0; under_list=[]; over_list=[]
    for pid, al in by.items():
        s = sub_by.get(pid)
        if not s or s.shifts_requested == 0: continue
        if len(al) < s.shifts_requested: under += 1; under_list.append(f"{pid} {len(al)}/{s.shifts_requested}")
        if len(al) > s.shifts_requested: over += 1; over_list.append(f"{pid} {len(al)}/{s.shifts_requested}")
    m["below requested total"] = (under, under_list); m["above requested total"] = (over, over_list)
    m["submitted but 0 shifts"] = [p for p, s in sub_by.items() if s.shifts_requested > 0 and p not in by]
    # anchors
    unmet=[]; over_req=[]; over_target=[]; floor_short=[]; blank_zero=[]; shares=[]
    for pid, al in by.items():
        s = sub_by.get(pid); c = roster.get(pid)
        if not s: continue
        n=len(al); a6=sum(1 for a in al if a.shift.time=="0600h"); a24=sum(1 for a in al if a.shift.time=="2400h")
        r6 = s.shifts_0600h_requested if s.shifts_0600h_stated else 0
        r24 = s.shifts_2400h_requested if s.shifts_2400h_stated else 0
        eff = s.shifts_requested or min(10, s.shifts_max)
        target = max(r6+r24, math.floor(share*eff))
        only = c and (c.only_0600h or c.only_2400h)
        exempt = c and getattr(c, "anchor_floor_exempt", False)
        if only: target = max(target, eff + tol)
        if exempt: target = r6 + r24
        if s.shifts_0600h_stated and a6 < s.shifts_0600h_requested: unmet.append(f"{pid} 0600 {a6}/{s.shifts_0600h_requested}")
        if s.shifts_2400h_stated and a24 < s.shifts_2400h_requested: unmet.append(f"{pid} 2400 {a24}/{s.shifts_2400h_requested}")
        if (s.shifts_0600h_stated and a6 > r6) or (s.shifts_2400h_stated and a24 > r24): over_req.append(f"{pid} +{max(0,a6-r6)}/+{max(0,a24-r24)}")
        if a6+a24 > target: over_target.append(f"{pid} {a6+a24}>{target}")
        if not only: shares.append(100*(a6+a24)/n)
        if r6+r24 == 0 and not exempt and not only:
            fl = 2 if n >= 8 else 1
            if a6+a24 < fl: floor_short.append(f"{pid} {a6+a24}/{fl} ({n} shifts)")
    m["unmet anchor requests"] = unmet
    m["over stated per-type request"] = (len(over_req), over_req)
    m["over fair-share target"] = (len(over_target), over_target)
    m["no-request physicians below floor"] = (len(floor_short), floor_short)
    m["anchor share: median / max (excl. only_*)"] = f"{statistics.median(shares):.0f}% / {max(shares):.0f}%" if shares else "n/a"
    # weekends
    over_we=[]; splits=0; we_days=Counter()
    for pid, al in by.items():
        s = sub_by.get(pid); c = roster.get(pid)
        clusters = defaultdict(set)
        for a in al:
            k = we_key(a.date)
            if k: clusters[k].add(a.date.weekday()); we_days[pid]+=1
        if c and c.prefer_weekends: cap=None
        elif c and c.max_weekends is not None: cap=c.max_weekends
        else: cap = max(1, math.floor(((s.shifts_requested if s else 0)/ft)*max_we + 0.5))
        if cap is not None and len(clusters) > cap: over_we.append(len(clusters)-cap)
        splits += sum(1 for d in clusters.values() if 4 in d and 6 in d and 5 not in d)
    m["over weekend cap (count; by 1/2/3+)"] = f"{len(over_we)}; {Counter(min(x,3) for x in over_we).get(1,0)}/{Counter(min(x,3) for x in over_we).get(2,0)}/{Counter(min(x,3) for x in over_we).get(3,0)}"
    m["Fri+Sun-without-Sat splits"] = splits
    m["weekend days per physician: mean / max"] = f"{statistics.mean(we_days.values()):.1f} / {max(we_days.values())}" if we_days else "n/a"
    # acute sides / runs
    rep = acute_balance.acute_side_report(res)
    m["A/B lopsided (0 vs >=3)"] = sum(1 for a,b in rep.values() if min(a,b)==0 and max(a,b)>=3)
    m["|A-B| >= 3"] = sum(1 for a,b in rep.values() if abs(a-b)>=3)
    runs=Counter(); noac=0; siterep=0; nehcrep=0; iso_nights=0; spacing=0
    for pid, al in by.items():
        days = {a.date: a for a in al}; ds = sorted(days)
        i=0
        while i < len(ds):
            j=i
            while j+1 < len(ds) and (ds[j+1]-ds[j]).days==1: j+=1
            run=[days[d] for d in ds[i:j+1]]; L=len(run); runs[L]+=1
            if L>=2 and not any(a.shift.site in ("RAH A side","RAH B side") for a in run): noac+=1
            sites=[a.shift.site for a in run]
            for s_ in set(sites):
                if sites.count(s_)>=2:
                    if s_=="NEHC": nehcrep+=1
                    else: siterep+=1
            i=j+1
        for d, a in days.items():
            if a.shift.time=="2400h":
                nb = [days.get(d+datetime.timedelta(days=o)) for o in (-1,1)]
                if not any(x and x.shift.time=="2400h" for x in nb): iso_nights+=1
            nxt = days.get(d+datetime.timedelta(days=1))
            if nxt and a.shift.start_hour >= 16 and nxt.shift.time=="0600h": spacing+=1
    m["runs: 1/2/3/4+ days"] = f"{runs[1]}/{runs[2]}/{runs[3]}/{sum(v for k,v in runs.items() if k>=4)}"
    m["runs of 2+ with no acute"] = noac
    m["same RAH site twice in a run / NEHC twice"] = f"{siterep} / {nehcrep}"
    m["isolated nights"] = iso_nights
    m["evening/night -> 0600h next day"] = spacing
    m["group A share"] = f"{sum(1 for a in res.assignments if a.shift.site_group.value=='A')/len(res.assignments):.1%}"
    # ---------------- on-call ----------------
    worked = defaultdict(set)
    for a in res.assignments: worked[a.physician_id].add(a.date)
    days = sorted({a.date for a in res.assignments} | {u.date for u in res.unfilled})
    total_call_slots = 2 * len(days)
    we_iso = we_tot = mid_iso = mid_tot = 0; same_day = 0; per_phys = Counter(); no_call_flag = 0; iso_list = []
    for oc in res.on_calls:
        per_phys[oc.physician_id] += 1
        if oc.date in worked.get(oc.physician_id, set()): same_day += 1
        c = roster.get(oc.physician_id)
        if c and c.no_call: no_call_flag += 1
        k = we_key(oc.date)
        if k:
            we_tot += 1
            cluster = {k + datetime.timedelta(days=i) for i in range(3)}
            if not (worked.get(oc.physician_id, set()) & cluster): we_iso += 1; iso_list.append(f"{oc.physician_id} {oc.date.strftime('%a %d')} {oc.call_type}")
        else:
            mid_tot += 1
            near = {oc.date + datetime.timedelta(days=d) for d in (-2, -1, 1, 2)}
            if not (worked.get(oc.physician_id, set()) & near): mid_iso += 1; iso_list.append(f"{oc.physician_id} {oc.date.strftime('%a %d')} {oc.call_type}")
    m["on-call filled / slots"] = f"{len(res.on_calls)} / {total_call_slots}"
    m["weekend calls isolated (no shift that Fri-Sun)"] = f"{we_iso} / {we_tot}"
    m["midweek calls isolated (no shift within 2 days)"] = f"{mid_iso} / {mid_tot}"
    m["call on a day with a regular shift"] = same_day
    m["physicians with 2+ calls"] = sum(1 for v in per_phys.values() if v >= 2)
    m["calls given to no_call physicians"] = no_call_flag
    m["isolated calls (list)"] = iso_list
    return m

files = [T/"cpsatv2-jan7.xlsx", T/"cpsatv2-jan6.xlsx", T/"January 2027 human.xlsx"]
results = {}
for f in files:
    unres.clear()
    results[f.name] = metrics(load(f))
    if unres: results[f.name]["UNRESOLVED NAMES"] = dict(unres)
keys = list(results[files[0].name].keys())
for k2 in results:
    for k in results[k2]:
        if k not in keys: keys.append(k)
w = 46
print(f"{'metric':46}| {'jan7':34}| {'jan6':34}| human")
print("-"*160)
for k in keys:
    vals = []
    for f in files:
        v = results[f.name].get(k, "")
        if isinstance(v, tuple): v = f"{v[0]}" + (f" [{', '.join(v[1][:4])}{'…' if len(v[1])>4 else ''}]" if v[1] else "")
        elif isinstance(v, list): v = f"{len(v)}" + (f" [{', '.join(map(str,v[:4]))}{'…' if len(v)>4 else ''}]" if v else "")
        vals.append(str(v))
    print(f"{k:46}| {vals[0]:34.34}| {vals[1]:34.34}| {vals[2]}")
print("\nFULL LISTS:")
for f in files:
    print(f"\n== {f.name}")
    for k in ("below requested total","above requested total","unmet anchor requests","over fair-share target","no-request physicians below floor","submitted but 0 shifts","isolated calls (list)"):
        v = results[f.name].get(k)
        if isinstance(v, tuple): v = v[1]
        if v: print(f"  {k}: {v}")
