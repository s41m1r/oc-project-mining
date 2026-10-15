"""Share of tracker change events that involve more than one object (TAWOS).

A change event involves several objects if its field refers to another object:
another issue (Link, Epic Link/Child, Parent), a release (Fix Version, Version),
a sprint, a user (assignee, reporter), a component, or a project.
Writes results/multi_object_events.csv.
"""
import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "tawos.sqlite"
OUT = ROOT / "results" / "multi_object_events.csv"

MULTI = {"Link": "issue", "Epic Link": "epic", "Epic Child": "issue", "Parent": "issue",
         "Parent Issue": "issue", "Fix Version": "release", "Version": "release",
         "Sprint": "sprint", "assignee": "user", "reporter": "user",
         "Component": "component", "project": "project"}
# Fields the technique uses as evidence or lifecycle (Sect. 3.2).
USED = set(MULTI) | {"status", "resolution", "Story Points", "issuetype"}

db = sqlite3.connect(DB)
rows = db.execute("SELECT Field, COUNT(*) FROM Change_Log GROUP BY Field").fetchall()
total = sum(n for _, n in rows)
multi = sum(n for f, n in rows if f in MULTI)
used = sum(n for f, n in rows if f in USED)
used_multi = sum(n for f, n in rows if f in USED and f in MULTI)

print(f"all change events:              {total:>10,}")
print(f"  involving several objects:    {multi:>10,}  ({100*multi/total:.1f}%)")
print(f"events used by the technique:   {used:>10,}")
print(f"  involving several objects:    {used_multi:>10,}  ({100*used_multi/used:.1f}%)")
print("\nmulti-object fields:")
for f, n in sorted(rows, key=lambda r: -r[1]):
    if f in MULTI:
        print(f"  {f:14s} -> {MULTI[f]:9s} {n:>9,}")

OUT.parent.mkdir(exist_ok=True)
with open(OUT, "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["field", "events", "other_object_type", "used_by_technique"])
    for f, n in sorted(rows, key=lambda r: -r[1]):
        w.writerow([f, n, MULTI.get(f, ""), f in USED])
