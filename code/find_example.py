"""Search for a small running example: a cluster of 6-8 issues of one release with
>= 2 declared Temporal/Causal links and >= 1 unlinked textual mention between them."""
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "tawos.sqlite"
PROJECTS = ("MESOS", "TIMOB", "SERVER", "MDL", "FAB", "TISTUD", "EVG")
TEMPORAL = ("Blocks", "Blocker", "Blockers", "Depends", "Dependency", "Gantt: start-finish")
CUE = re.compile(r"\b(after|before|once|blocked|blocks|depends|depend|requires|needs|first|until|prerequisite|follow[- ]?up|land)\b", re.I)

db = sqlite3.connect(DB)
releases = db.execute(f"""
  SELECT v.ID, p.Project_Key, v.Name, v.Release_Date FROM Version v JOIN Project p ON p.ID = v.Project_ID
  WHERE p.Project_Key IN ({",".join("?" * len(PROJECTS))}) AND v.Release_Date IS NOT NULL""", PROJECTS).fetchall()

found = []
for vid, proj, vname, rdate in releases:
    issues = {r[0]: r[1:] for r in db.execute("""
        SELECT i.ID, i.Issue_Key, i.Type, i.Title, i.Story_Point, i.Assignee_ID FROM Issue i
        JOIN Fix_Version f ON f.Issue_ID = i.ID WHERE f.Fix_Version_ID = ?""", (vid,))}
    if len(issues) < 6 or len(issues) > 400:
        continue
    keys = {v[0]: k for k, v in issues.items()}
    links = db.execute(f"""
        SELECT Issue_ID, Target_Issue_ID, Name FROM Issue_Link WHERE Direction = 'OUTBOUND'
        AND Name IN ({",".join("?" * len(TEMPORAL))})""", TEMPORAL).fetchall()
    links = [(a, b, n) for a, b, n in links if a in issues and b in issues]
    if len(links) < 2:
        continue
    linked_pairs = {frozenset((a, b)) for a, b, _ in db.execute(
        f"SELECT Issue_ID, Target_Issue_ID, Name FROM Issue_Link WHERE Issue_ID IN ({','.join(map(str, issues))})")}
    # textual mentions of another issue of the same release without any link
    mentions = []
    keyre = re.compile(r"\b(" + "|".join(map(re.escape, keys)) + r")\b")
    q = f"""SELECT Issue_ID, Comment_Text FROM Comment WHERE Issue_ID IN ({','.join(map(str, issues))})
            UNION ALL SELECT ID, Description_Text FROM Issue WHERE ID IN ({','.join(map(str, issues))})"""
    for iid, text in db.execute(q):
        if not text:
            continue
        for m in keyre.finditer(text):
            other = keys[m.group(1)]
            if other == iid or frozenset((iid, other)) in linked_pairs:
                continue
            ctx = text[max(0, m.start() - 150): m.end() + 150]
            if CUE.search(ctx):
                mentions.append((iid, other, ctx.replace("\n", " ")))
    # clusters: connected components over declared links + mentions
    adj = defaultdict(set)
    for a, b, _ in links:
        adj[a].add(b); adj[b].add(a)
    for a, b, _ in mentions:
        adj[a].add(b); adj[b].add(a)
    seen = set()
    for start in list(adj):
        if start in seen:
            continue
        comp, stack = set(), [start]
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x); stack.extend(adj[x] - comp)
        seen |= comp
        nl = sum(1 for a, b, _ in links if a in comp)
        nm = [m for m in mentions if m[0] in comp]
        if 4 <= len(comp) <= 9 and nl >= 2 and nm:
            found.append((proj, vname, rdate, len(issues), sorted(issues[i][0] for i in comp), nl, nm))

for proj, vname, rdate, n, comp, nl, nm in found:
    print(f"\n{proj} {vname} ({rdate}, {n} issues): {len(comp)} issues, {nl} links, {len(nm)} mentions")
    print("  ", " ".join(comp))
    for a, b, ctx in nm[:3]:
        print(f"   [{a}->{b}] ...{ctx[:300]}...")
