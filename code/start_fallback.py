"""For resolved issues without an active-status start: is there another work
signal? Signal = first comment or field change authored by the final assignee
(an event involving issue and user object). Also: share without start among
endpoints of Block/Depend links. Prints a table; extends start_signals.py."""
import sqlite3, statistics
from pathlib import Path
from start_signals import PROJECTS, ACTIVE, DONE_RES, ts

DB = Path(__file__).resolve().parents[1] / "data" / "tawos.sqlite"
con = sqlite3.connect(DB)
for pid, key in PROJECTS.items():
    act = ACTIVE[key]
    iss = {i: (ts(r), a) for i, r, a in con.execute(
        f"select ID, Resolution_Date, Assignee_ID from Issue where Project_ID=? and Resolution in "
        f"({','.join('?'*len(DONE_RES))}) and Resolution_Date is not null", (pid, *DONE_RES))}
    start = {}
    for i, to, d in con.execute("select e.Issue_ID,e.To_String,e.Creation_Date from Event e join Issue i "
            "on i.ID=e.Issue_ID where i.Project_ID=? and e.Field='status' order by e.Creation_Date", (pid,)):
        if to in act and i not in start: start[i] = ts(d)
    nostart = {i for i, (r, _) in iss.items() if i not in start or start[i] > r}
    first_asg = {}
    q = ("select x.Issue_ID, x.Author_ID, x.Creation_Date from ({}) x join Issue i on i.ID=x.Issue_ID "
         "where i.Project_ID=?")
    for sub in ("select Issue_ID, Author_ID, Creation_Date from Event",
                "select Issue_ID, Author_ID, Creation_Date from Comment"):
        for i, au, d in con.execute(q.format(sub), (pid,)):
            if i in nostart and au is not None and au == iss[i][1]:
                t = ts(d)
                if t <= iss[i][0] and (i not in first_asg or t < first_asg[i]):
                    first_asg[i] = t
    gaps = [(iss[i][0] - t).total_seconds() / 86400 for i, t in first_asg.items()]
    linked = {r for (r,) in con.execute(
        "select l.Issue_ID from Issue_Link l join Issue i on i.ID=l.Issue_ID where i.Project_ID=? "
        "and (l.Name like 'Block%' or l.Name like 'Depend%')", (pid,))} & set(iss)
    print(f"{key}: no_start={len(nostart)}  with_assignee_signal={len(first_asg)} "
          f"({100*len(first_asg)/max(1,len(nostart)):.0f}%), median gap to resolution="
          f"{statistics.median(gaps) if gaps else 0:.1f} d, gap<1h={100*sum(g<1/24 for g in gaps)/max(1,len(gaps)):.0f}% | "
          f"linked resolved={len(linked)}, linked no_start={100*len(linked & nostart)/max(1,len(linked)):.0f}%")
