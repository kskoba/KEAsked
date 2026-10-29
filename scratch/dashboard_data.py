"""Build the JSON datasets for the jan7-vs-human dashboard, reusing compare_jan5.py's loaders/metrics."""
import json, datetime, math, statistics, sys
from collections import Counter, defaultdict
from pathlib import Path
src = Path("scratch/compare_jan5.py").read_text()
head = src[:src.index("files = [")]
g = {}
exec(compile(head, "compare_head", "exec"), g)
load, metrics, we_key, roster, sub_by = g["load"], g["metrics"], g["we_key"], g["roster"], g["sub_by"]
T = g["T"]
OUT = Path("/home/cid/.claude/jobs/3e5d00b4/tmp/dash")
res = {"jan7": load(T / "cpsatv2-jan7.xlsx"), "human": load(T / "January 2027 human.xlsx")}
m = {k: metrics(v) for k, v in res.items()}

def num(v):
    return v[0] if isinstance(v, tuple) else (len(v) if isinstance(v, list) else v)

# --- summary metrics: one row per metric, with the direction that is better
LOWER = "lower"; HIGHER = "higher"; NEUTRAL = ""
spec = [
    ("Coverage", "Slots filled", "filled", HIGHER),
    ("Coverage", "Slots unfilled (Jan 1 belongs to December)", "unfilled", NEUTRAL),
    ("Requests", "Physicians below requested total", "below requested total", LOWER),
    ("Requests", "Physicians above requested total", "above requested total", LOWER),
    ("Requests", "Submitted but given 0 shifts", "submitted but 0 shifts", LOWER),
    ("Anchors", "Unmet stated 0600h/2400h requests", "unmet anchor requests", LOWER),
    ("Anchors", "Over a stated per-type request", "over stated per-type request", LOWER),
    ("Anchors", "Over fair-share anchor target", "over fair-share target", LOWER),
    ("Anchors", "No-request physicians below the floor", "no-request physicians below floor", LOWER),
    ("Anchors", "Anchor share, median / max", "anchor share: median / max (excl. only_*)", NEUTRAL),
    ("Weekends", "Over weekend cap (by 1 / 2 / 3+)", "over weekend cap (count; by 1/2/3+)", LOWER),
    ("Weekends", "Fri+Sun without Saturday", "Fri+Sun-without-Sat splits", LOWER),
    ("Weekends", "Weekend days per physician, mean / max", "weekend days per physician: mean / max", NEUTRAL),
    ("Acute sites", "RAH A/B lopsided (0 on one side, 3+ on the other)", "A/B lopsided (0 vs >=3)", LOWER),
    ("Acute sites", "RAH A vs B gap of 3 or more", "|A-B| >= 3", LOWER),
    ("Acute sites", "Group A share of all shifts", "group A share", NEUTRAL),
    ("Runs", "Runs of 1 / 2 / 3 / 4+ consecutive days", "runs: 1/2/3/4+ days", NEUTRAL),
    ("Runs", "Runs of 2+ days with no acute shift", "runs of 2+ with no acute", LOWER),
    ("Runs", "Same RAH site twice in a run / NEHC twice", "same RAH site twice in a run / NEHC twice", LOWER),
    ("Runs", "Isolated nights (no adjacent night)", "isolated nights", LOWER),
    ("Runs", "Evening or night followed by 0600h next day", "evening/night -> 0600h next day", LOWER),
    ("On-call", "Calls filled / slots", "on-call filled / slots", HIGHER),
    ("On-call", "Weekend calls with no shift that Fri-Sun", "weekend calls isolated (no shift that Fri-Sun)", LOWER),
    ("On-call", "Midweek calls with no shift within 2 days", "midweek calls isolated (no shift within 2 days)", LOWER),
    ("On-call", "Call on a day with a regular shift", "call on a day with a regular shift", LOWER),
    ("On-call", "Physicians with 2 or more calls", "physicians with 2+ calls", LOWER),
    ("On-call", "Calls given to no-call physicians", "calls given to no_call physicians", LOWER),
]
def first_int(v):
    s = str(num(v)); 
    import re
    mm = re.match(r"\s*(\d+)", s); return int(mm.group(1)) if mm else None
rows = []
for group, label, key, direction in spec:
    a, b = m["jan7"][key], m["human"][key]
    va, vb = num(a), num(b)
    ia, ib = first_int(a), first_int(b)
    better = ""
    if direction and ia is not None and ib is not None and ia != ib:
        better = "solver" if ((ia < ib) == (direction == LOWER)) else "human"
    rows.append({"group": group, "metric": label, "solver": str(va), "human": str(vb), "better": better})
json.dump(rows, open(OUT / "metrics.json", "w"), indent=1)

# --- per-physician
def per_phys(r):
    by = defaultdict(list)
    for a in r.assignments: by[a.physician_id].append(a)
    out = {}
    for pid, al in by.items():
        clusters = defaultdict(set)
        for a in al:
            k = we_key(a.date)
            if k: clusters[k].add(a.date.weekday())
        out[pid] = dict(shifts=len(al), nights=sum(1 for a in al if a.shift.time == "2400h"),
                        mornings=sum(1 for a in al if a.shift.time == "0600h"),
                        weekend_days=sum(len(v) for v in clusters.values()), weekends=len(clusters),
                        rah_a=sum(1 for a in al if a.shift.site == "RAH A side"), rah_b=sum(1 for a in al if a.shift.site == "RAH B side"))
    return out
pp = {k: per_phys(v) for k, v in res.items()}
prow = []
for pid in sorted(set(pp["jan7"]) | set(pp["human"]), key=str.lower):
    s = sub_by.get(pid); c = roster.get(pid)
    a = pp["jan7"].get(pid, {}); b = pp["human"].get(pid, {})
    prow.append({"physician": pid,
                 "requested": s.shifts_requested if s else None,
                 "stated_2400h": (s.shifts_2400h_requested if s and s.shifts_2400h_stated else 0) if s else None,
                 "stated_0600h": (s.shifts_0600h_requested if s and s.shifts_0600h_stated else 0) if s else None,
                 "solver_shifts": a.get("shifts", 0), "human_shifts": b.get("shifts", 0),
                 "solver_nights": a.get("nights", 0), "human_nights": b.get("nights", 0),
                 "solver_0600h": a.get("mornings", 0), "human_0600h": b.get("mornings", 0),
                 "solver_weekend_days": a.get("weekend_days", 0), "human_weekend_days": b.get("weekend_days", 0),
                 "solver_weekends": a.get("weekends", 0), "human_weekends": b.get("weekends", 0),
                 "solver_rah_a": a.get("rah_a", 0), "solver_rah_b": a.get("rah_b", 0),
                 "human_rah_a": b.get("rah_a", 0), "human_rah_b": b.get("rah_b", 0),
                 "prefer_weekends": bool(c and c.prefer_weekends)})
json.dump(prow, open(OUT / "physicians.json", "w"), indent=1)

# --- weekend shapes
def shapes(r):
    cl = defaultdict(set)
    for a in r.assignments:
        k = we_key(a.date)
        if k: cl[(a.physician_id, k)].add(a.date.weekday())
    names = {4: "F", 5: "S", 6: "U"}
    return Counter("".join(names[d] for d in sorted(v)) for v in cl.values())
sh = {k: shapes(v) for k, v in res.items()}
label = {"FSU": "Fri+Sat+Sun", "FS": "Fri+Sat", "SU": "Sat+Sun", "FU": "Fri+Sun (split)", "F": "Friday only", "S": "Saturday only", "U": "Sunday only"}
json.dump([{"shape": label[k], "code": k, "solver": sh["jan7"].get(k, 0), "human": sh["human"].get(k, 0)} for k in ("FSU", "FS", "SU", "FU", "F", "S", "U")],
          open(OUT / "weekend_shapes.json", "w"), indent=1)

# --- issue lists
lists = []
for sched, key, cat in [(s_, k_, c_) for s_ in ("jan7", "human") for k_, c_ in (
        ("below requested total", "Below requested total"), ("unmet anchor requests", "Unmet anchor request"),
        ("over stated per-type request", "Over stated anchor request"), ("over fair-share target", "Over fair-share target"),
        ("no-request physicians below floor", "Below anchor floor"), ("isolated calls (list)", "Isolated call"),
        ("submitted but 0 shifts", "Submitted, 0 shifts"))]:
    v = m[sched][key]; items = v[1] if isinstance(v, tuple) else v
    for it in items:
        lists.append({"schedule": "solver" if sched == "jan7" else "human", "category": cat, "detail": it})
json.dump(lists, open(OUT / "issues.json", "w"), indent=1)

# --- anchor supply vs demand (context)
nights_req = sum((s.shifts_2400h_requested or 0) if s.shifts_2400h_stated else 0 for s in sub_by.values())
morn_req = sum((s.shifts_0600h_requested or 0) if s.shifts_0600h_stated else 0 for s in sub_by.values())
json.dump([{"type": "2400h nights", "slots": 120, "stated_requests": nights_req},
           {"type": "0600h mornings", "slots": 120, "stated_requests": morn_req},
           {"type": "all shifts", "slots": 630, "stated_requests": sum(s.shifts_requested for s in sub_by.values())}],
          open(OUT / "supply.json", "w"), indent=1)
print("written:", sorted(p.name for p in OUT.iterdir()))
print("metrics rows", len(rows), "physicians", len(prow), "issues", len(lists))
