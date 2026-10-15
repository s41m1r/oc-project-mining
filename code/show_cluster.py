"""Print everything about a set of issues: attributes, links (with dates), epic/parent,
status timeline, assignee changes, and comments mentioning the other issues.
Usage: python3 show_cluster.py KEY1 KEY2 ..."""
import re
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "tawos.sqlite"
db = sqlite3.connect(DB)
keys = sys.argv[1:]
ph = ",".join("?" * len(keys))
ids = {r[0]: r[1] for r in db.execute(f"SELECT ID, Issue_Key FROM Issue WHERE Issue_Key IN ({ph})", keys)}
idph = ",".join(map(str, ids))

print("== issues")
for r in db.execute(f"""
    SELECT i.Issue_Key, i.Type, i.Priority, i.Status, i.Story_Point, i.Assignee_ID, i.Sprint_ID,
           i.Creation_Date, i.Resolution_Date, round(i.In_Progress_Minutes/1440.0,1),
           (SELECT group_concat(v.Name) FROM Fix_Version f JOIN Version v ON v.ID=f.Fix_Version_ID WHERE f.Issue_ID=i.ID),
           (SELECT group_concat(c.Name) FROM Issue_Component ic JOIN Component c ON c.ID=ic.Component_ID WHERE ic.Issue_ID=i.ID),
           i.Title
    FROM Issue i WHERE i.ID IN ({idph}) ORDER BY i.Issue_Key"""):
    print(" | ".join(str(x) for x in r))

print("\n== links (all types touching these issues)")
for r in db.execute(f"""
    SELECT s.Issue_Key, l.Description, t.Issue_Key, l.Name FROM Issue_Link l
    JOIN Issue s ON s.ID=l.Issue_ID JOIN Issue t ON t.ID=l.Target_Issue_ID
    WHERE l.Direction='OUTBOUND' AND (l.Issue_ID IN ({idph}) OR l.Target_Issue_ID IN ({idph}))"""):
    inside = r[0] in keys and r[2] in keys
    print(("  * " if inside else "    ") + f"{r[0]} {r[1]} {r[2]}  [{r[3]}]")

print("\n== events (status, assignee, link, epic, parent, sprint, fix version, SP)")
for r in db.execute(f"""
    SELECT i.Issue_Key, e.Creation_Date, e.Field, e.From_String, e.To_String, e.Author_ID FROM Event e
    JOIN Issue i ON i.ID=e.Issue_ID WHERE e.Issue_ID IN ({idph})
    AND e.Field IN ('status','assignee','Link','Epic Link','Parent','Parent Issue','Sprint','Fix Version','Story Points')
    ORDER BY e.Creation_Date"""):
    print(f"  {r[1]}  {r[0]:12s} {r[2]:12s} {str(r[3])[:40]!s:40s} -> {str(r[4])[:60]}  (u{r[5]})")

print("\n== comments/descriptions mentioning another issue of the set")
keyre = re.compile(r"\b(" + "|".join(map(re.escape, keys)) + r")\b")
for iid, date, author, text in db.execute(f"""
    SELECT Issue_ID, Creation_Date, Author_ID, Comment_Text FROM Comment WHERE Issue_ID IN ({idph})
    UNION ALL SELECT ID, Creation_Date, Reporter_ID, Description_Text FROM Issue WHERE ID IN ({idph})
    ORDER BY 2"""):
    if text and any(m.group(1) != ids[iid] for m in keyre.finditer(text)):
        print(f"  [{ids[iid]} {date} u{author}] {text.strip()[:600]}\n")
