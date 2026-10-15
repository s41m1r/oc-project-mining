"""First data checks on TAWOS, per project.

1. Temporal/Causal links (Lüders et al. 2022: Block, Depend, plus Gantt/precede).
2. Whether those links have a creation timestamp in Change_Log (Field='Link').

Needs the Event table (slim copy of Change_Log), built by build_events.sql.
3. Completeness of fix versions and "in progress" transitions; parents of sub-tasks.

Writes results/data_checks.csv and prints it.
"""
import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "tawos.sqlite"
OUT = ROOT / "results" / "data_checks.csv"

# Link names in the Temporal/Causal category; one row per pair (OUTBOUND side).
TEMPORAL = ("Blocks", "Blocker", "Blockers", "Depends", "Dependency",
            "Gantt: start-finish", "Gantt: end-start", "Gantt: start-start",
            "Gantt: end-end", "Precedes", "Prerequisite")
# Status names that mean work has started (first such transition = start).
STARTED = ("In Progress", "Development in progress", "In Development",
           "In Code Review", "In Review", "Peer review in progress",
           "Waiting for peer review", "Testing in progress", "Waiting for testing",
           "Waiting for integration review", "Integration review in progress")

PH = lambda xs: ",".join("?" * len(xs))

# Materialized step by step; one big CTE made SQLite pick a pathological plan.
STEPS = [
    ("tc", f"""
     SELECT l.Issue_ID AS src, l.Target_Issue_ID AS dst, s.Project_ID AS pid,
            s.Issue_Key AS skey, t.Issue_Key AS tkey,
            s.Project_ID = t.Project_ID AS same_project
     FROM Issue_Link l
     JOIN Issue s ON s.ID = l.Issue_ID
     JOIN Issue t ON t.ID = l.Target_Issue_ID
     WHERE l.Direction = 'OUTBOUND' AND l.Name IN ({PH(TEMPORAL)})""", TEMPORAL),
    ("link_events", """
     SELECT DISTINCT Issue_ID, To_Value AS key FROM Event
     WHERE Field = 'Link' AND To_Value IS NOT NULL""", ()),
    ("started", f"""
     SELECT DISTINCT Issue_ID FROM Event
     WHERE Field = 'status' AND To_String IN ({PH(STARTED)})""", STARTED),
    ("fv", "SELECT DISTINCT Issue_ID FROM Fix_Version", ()),
    ("parented", """
     SELECT DISTINCT Issue_ID FROM Event WHERE Field IN ('Parent', 'Parent Issue')""", ()),
]

SQL = """
WITH tc_dated AS (
  SELECT tc.*,
         (EXISTS (SELECT 1 FROM link_events e WHERE e.Issue_ID = tc.src AND e.key = tc.tkey)
       OR EXISTS (SELECT 1 FROM link_events e WHERE e.Issue_ID = tc.dst AND e.key = tc.skey)) AS dated
  FROM tc
),
issues AS (
  SELECT i.Project_ID AS pid, COUNT(*) AS n_issues,
         SUM(i.Resolution_Date IS NOT NULL) AS n_resolved,
         SUM(fv.Issue_ID IS NOT NULL) AS n_fixversion,
         SUM(st.Issue_ID IS NOT NULL) AS n_started,
         SUM(i.Story_Point IS NOT NULL) AS n_sp,
         SUM(i.Sprint_ID IS NOT NULL) AS n_sprint,
         SUM(i.Type IN ('Sub-task', 'Technical task')) AS n_subtask,
         SUM(i.Type IN ('Sub-task', 'Technical task') AND pa.Issue_ID IS NOT NULL) AS n_subtask_parent
  FROM Issue i
  LEFT JOIN fv ON fv.Issue_ID = i.ID
  LEFT JOIN started st ON st.Issue_ID = i.ID
  LEFT JOIN parented pa ON pa.Issue_ID = i.ID
  GROUP BY i.Project_ID
),
links AS (
  SELECT pid, COUNT(*) AS n_tc, SUM(same_project) AS n_tc_internal, SUM(dated) AS n_tc_dated
  FROM tc_dated GROUP BY pid
),
versions AS (
  SELECT Project_ID AS pid, COUNT(*) AS n_versions,
         SUM(Released = 1 AND Release_Date IS NOT NULL) AS n_released
  FROM Version GROUP BY Project_ID
)
SELECT p.Project_Key AS project, r.Name AS repo, issues.*,
       COALESCE(links.n_tc, 0) AS n_tc, COALESCE(links.n_tc_internal, 0) AS n_tc_internal,
       COALESCE(links.n_tc_dated, 0) AS n_tc_dated,
       COALESCE(versions.n_versions, 0) AS n_versions, COALESCE(versions.n_released, 0) AS n_released
FROM Project p
LEFT JOIN Repository r ON r.ID = p.Repository_ID
JOIN issues ON issues.pid = p.ID
LEFT JOIN links ON links.pid = p.ID
LEFT JOIN versions ON versions.pid = p.ID
ORDER BY n_tc DESC
"""


def main():
    db = sqlite3.connect(DB)
    for name, sql, params in STEPS:
        db.execute(f"CREATE TEMP TABLE {name} AS {sql}", params)
        db.execute(f"CREATE INDEX temp.ix_{name} ON {name} (Issue_ID)" if name != "tc"
                   else "CREATE INDEX temp.ix_tc ON tc (src)")
    db.execute("CREATE INDEX temp.ix_link_events_key ON link_events (Issue_ID, key)")
    cur = db.execute(SQL)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="") as f:
        csv.writer(f).writerows([cols] + rows)

    pct = lambda a, b: f"{100 * a / b:3.0f}%" if b else "  - "
    print(f"{'project':10s} {'issues':>7s} {'TC':>5s} {'int':>5s} {'dated':>5s} "
          f"{'fixv':>5s} {'start':>5s} {'SP':>5s} {'sprnt':>5s} {'subt':>5s} {'par':>5s} {'rel':>4s}")
    for r in rows:
        d = dict(zip(cols, r))
        n = d["n_issues"]
        print(f"{d['project']:10s} {n:7d} {d['n_tc']:5d} {pct(d['n_tc_internal'], d['n_tc'])} "
              f"{pct(d['n_tc_dated'], d['n_tc'])} {pct(d['n_fixversion'], n)} "
              f"{pct(d['n_started'], n)} {pct(d['n_sp'], n)} {pct(d['n_sprint'], n)} "
              f"{d['n_subtask']:5d} {pct(d['n_subtask_parent'], d['n_subtask'])} {d['n_released']:4d}")


if __name__ == "__main__":
    main()
