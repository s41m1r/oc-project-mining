"""How reliable is the start of a task? Writes results/start_signals.csv.

For resolved issues (resolution Fixed/Done/Implemented/Resolved) of the
candidate projects, measures
  - no_start: no transition into an active status before resolution,
  - click_1h / click_1d: first active status within 1 hour / 1 day before
    resolution (signature of a status set late, at the end of the work),
  - sub_earlier: issues with sub-tasks whose first sub-task start precedes
    the parent's own first active status (late click recovered via the OCEL).
Active statuses correspond to Jira's "In Progress" status category.
"""
import csv
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DB = BASE / "data" / "tawos.sqlite"
OUT = BASE / "results" / "start_signals.csv"

PROJECTS = {4: "MESOS", 28: "DM", 33: "SERVER", 34: "MDL"}
ACTIVE = {
    "MESOS": {"In Progress", "Reviewable", "Ready To Review"},
    "DM": {"In Progress", "In Review", "Reviewed", "Review Complete",
           "Ready for Review", "Ready to Merge", "Admin Review", "Review",
           "QA Review"},
    "SERVER": {"In Progress", "In Code Review", "Investigating", "Needs Merge",
               "Needs Reviewer", "Researching", "In Review", "Debugging",
               "Debugging With Submitter", "Needs Verification"},
    "MDL": {"Development in progress", "Waiting for peer review",
            "Peer review in progress", "Waiting for integration review",
            "Integration review in progress", "Waiting for testing",
            "Testing in progress", "Tested", "Problem during testing",
            "In Progress", "Ready for review", "Waiting for review",
            "Waiting for security testing", "Security testing in progress",
            "Security test failed", "Waiting for release"},
}
DONE_RES = ("Fixed", "Done", "Implemented", "Resolved")


def ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def main():
    con = sqlite3.connect(DB)
    rows = []
    for pid, key in PROJECTS.items():
        active = ACTIVE[key]
        issues = {i: (k, ts(r)) for i, k, r in con.execute(
            f"select ID, Issue_Key, Resolution_Date from Issue where Project_ID=? "
            f"and Resolution in ({','.join('?' * len(DONE_RES))}) "
            f"and Resolution_Date is not null", (pid, *DONE_RES))}
        first_active = {}
        for iid, to, d in con.execute(
                "select e.Issue_ID, e.To_String, e.Creation_Date from Event e "
                "join Issue i on i.ID=e.Issue_ID where i.Project_ID=? "
                "and e.Field='status' order by e.Creation_Date", (pid,)):
            if to in active and iid not in first_active:
                first_active[iid] = ts(d)
        # sub-task -> parent key (last assignment wins)
        key2id = {k: i for i, (k, _) in issues.items()}
        children = defaultdict(set)
        for iid, to in con.execute(
                "select e.Issue_ID, e.To_String from Event e join Issue i "
                "on i.ID=e.Issue_ID where i.Project_ID=? and e.Field in "
                "('Parent','Parent Issue') and e.To_String is not null "
                "and e.To_String<>'' order by e.Creation_Date", (pid,)):
            if to in key2id:
                children[key2id[to]].add(iid)

        n = len(issues)
        no_start = click_1h = click_1d = with_start = 0
        for iid, (_, res) in issues.items():
            a = first_active.get(iid)
            if a is None or a > res:
                no_start += 1
                continue
            with_start += 1
            gap = (res - a).total_seconds()
            click_1h += gap <= 3600
            click_1d += gap <= 86400
        parents = sub_earlier = 0
        for p, kids in children.items():
            starts = [first_active[c] for c in kids if c in first_active]
            if not starts:
                continue
            parents += 1
            own = first_active.get(p)
            if own is None or min(starts) < own:
                sub_earlier += 1
        rows.append(dict(project=key, resolved=n, no_start=no_start,
                         no_start_pct=round(100 * no_start / n, 1),
                         click_1h_pct=round(100 * click_1h / with_start, 1),
                         click_1d_pct=round(100 * click_1d / with_start, 1),
                         parents_with_started_subtasks=parents,
                         sub_earlier=sub_earlier))
        print(rows[-1])
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
