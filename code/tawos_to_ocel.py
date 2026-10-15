"""Step 1 of object-centric project mining: export a TAWOS tracker project
(Jira) to an OCEL 2.0 SQLite file, data/ocel/<KEY>.sqlite.

Object types: issue, release, sprint, component, user (epics are issues of
issue type Epic). Events: create issue, set status, resolve, unresolve,
add link, remove link, set fix version, remove fix version, add to sprint,
remove from sprint, set epic, set parent, add component, remove component,
assign, estimate, comment. Every event is related to all objects it
involves (event_object). object_object holds the final relations: links
(qualifier = link description of the source), part of (sub-task -> parent,
issue -> epic), fix version, sprint, component, assignee.

Only this step is tracker-specific; mining.py reads the OCEL only.

Usage: python tawos_to_ocel.py MDL DM SERVER MESOS
"""
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
TAWOS = BASE / "data" / "tawos.sqlite"
OUT_DIR = BASE / "data" / "ocel"

# Statuses of the in-progress category per tracker project (as in start_signals.py).
IN_PROGRESS = {
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
DONE_STATUS = {"Resolved", "Closed", "Done", "Completed"}
LINK_RE = re.compile(r"^This issue (.+) ([A-Z][A-Z0-9_]*-\d+)$")


def category(project, status):
    if status in IN_PROGRESS[project]:
        return "in progress"
    if status in DONE_STATUS:
        return "done"
    return "to do"


def q(con, sql, params=()):
    return pd.read_sql_query(sql, con, params=params)


def export(key):
    con = sqlite3.connect(TAWOS)
    pid = int(con.execute("select ID from Project where Project_Key=?", (key,)).fetchone()[0])

    # ---------------- objects ----------------
    iss = q(con, "select ID, Issue_Key, Title, Description_Text, Type, Priority, Status, "
                 "Resolution, Creation_Date, Resolution_Date, Story_Point, Assignee_ID, "
                 "Sprint_ID from Issue where Project_ID=?", (pid,))
    iss["oid"] = iss["Issue_Key"]
    id2key = dict(zip(iss["ID"], iss["Issue_Key"]))
    keys = set(iss["Issue_Key"])

    ver = q(con, "select ID, Jira_ID, Name, Release_Date, Released from Version where Project_ID=?", (pid,))
    ver["oid"] = "release:" + ver["ID"].astype(str)
    verjira2oid = dict(zip(ver["Jira_ID"].astype(str), ver["oid"]))
    spr = q(con, "select ID, JiraID, Name, Start_Date, End_Date, Activated_Date, Complete_Date "
                 "from Sprint where Project_ID=?", (pid,))
    spr["oid"] = "sprint:" + spr["ID"].astype(str)
    sprjira2oid = dict(zip(spr["JiraID"].astype(str), spr["oid"]))
    comp = q(con, "select ID, Name from Component where Project_ID=?", (pid,))
    comp["oid"] = "component:" + comp["ID"].astype(str)
    compname2oid = dict(zip(comp["Name"], comp["oid"]))

    ev = q(con, "select e.ID, e.Issue_ID, e.Field, e.From_Value, e.To_Value, e.From_String, "
                "e.To_String, e.Creation_Date, e.Author_ID from Event e join Issue i on "
                "i.ID=e.Issue_ID where i.Project_ID=? and e.Field in ('status','resolution',"
                "'Link','Parent','Parent Issue','Epic Link','Fix Version','Sprint','Component',"
                "'assignee','Story Points')", (pid,))
    ev["issue"] = ev["Issue_ID"].map(id2key)
    com = q(con, "select c.ID, c.Issue_ID, c.Comment_Text, c.Creation_Date, c.Author_ID from "
                 "Comment c join Issue i on i.ID=c.Issue_ID where i.Project_ID=?", (pid,))
    com["issue"] = com["Issue_ID"].map(id2key)

    users = set(iss["Assignee_ID"].dropna().astype(int)) | set(ev["Author_ID"].dropna().astype(int)) \
        | set(com["Author_ID"].dropna().astype(int))
    user_oid = lambda u: f"user:{int(u)}"

    # link directions for symmetric descriptions (from final Issue_Link state)
    lk = q(con, "select l.Issue_ID, l.Target_Issue_ID, l.Name, l.Direction, l.Description "
                "from Issue_Link l join Issue i on i.ID=l.Issue_ID where i.Project_ID=?", (pid,))
    lk["src"] = lk["Issue_ID"].map(id2key)
    lk["tgt"] = lk["Target_Issue_ID"].map(id2key)
    outbound = set(zip(lk.loc[lk.Direction == "OUTBOUND", "src"], lk.loc[lk.Direction == "OUTBOUND", "tgt"],
                       lk.loc[lk.Direction == "OUTBOUND", "Description"].str.lower()))
    desc2name = {}
    for d, n in zip(lk["Description"].str.lower(), lk["Name"]):
        desc2name.setdefault(d, n)
    con.close()

    # ---------------- events ----------------
    events = []      # (eid, type, time, attrs dict)
    e2o = []         # (eid, oid, qualifier)

    def add(eid, etype, t, attrs, rel):
        events.append((eid, etype, t, attrs))
        for oid, qual in rel:
            if oid is not None:
                e2o.append((eid, oid, qual))

    for r in iss.itertuples():
        add(f"create:{r.oid}", "create issue", r.Creation_Date, {}, [(r.oid, "issue")])

    seen_links = set()
    for r in ev.itertuples():
        eid = f"e{r.ID}"
        au = (user_oid(r.Author_ID), "author") if pd.notna(r.Author_ID) else (None, None)
        base = [(r.issue, "issue"), au]
        f = r.Field
        if f == "status":
            add(eid, "set status", r.Creation_Date,
                {"status": r.To_String, "category": category(key, r.To_String)}, base)
        elif f == "resolution":
            if r.To_String:
                add(eid, "resolve", r.Creation_Date, {"resolution": r.To_String}, base)
            else:
                add(eid, "unresolve", r.Creation_Date, {}, base)
        elif f == "Link":
            added = bool(r.To_String)
            text = r.To_String if added else r.From_String
            m = LINK_RE.match(text or "")
            if not m or m.group(2) not in keys:
                continue
            desc, other = m.group(1).lower(), m.group(2)
            # Jira logs a link change on both issues; keep it once.
            ident = (frozenset((r.issue, other)), desc2name.get(desc, desc), added, r.Creation_Date[:16])
            if ident in seen_links:
                continue
            seen_links.add(ident)
            # Store the change from the perspective of the source of the link.
            if (other, r.issue, _inverse_desc(desc, lk)) in outbound and (r.issue, other, desc) not in outbound:
                src, tgt, desc = other, r.issue, _inverse_desc(desc, lk)
            else:
                src, tgt = r.issue, other
            add(eid, "add link" if added else "remove link", r.Creation_Date,
                {"link_type": desc2name.get(desc, desc), "description": desc},
                [(src, "source"), (tgt, "target"), au])
        elif f in ("Parent", "Parent Issue"):
            if r.To_String in keys:
                add(eid, "set parent", r.Creation_Date, {}, base + [(r.To_String, "parent")])
        elif f == "Epic Link":
            if r.To_String in keys:
                add(eid, "set epic", r.Creation_Date, {}, base + [(r.To_String, "epic")])
            elif r.From_String in keys:
                add(eid, "remove epic", r.Creation_Date, {}, base + [(r.From_String, "epic")])
        elif f == "Fix Version":
            if r.To_Value:
                add(eid, "set fix version", r.Creation_Date, {"version": r.To_String},
                    base + [(verjira2oid.get(str(r.To_Value)), "release")])
            elif r.From_Value:
                add(eid, "remove fix version", r.Creation_Date, {"version": r.From_String},
                    base + [(verjira2oid.get(str(r.From_Value)), "release")])
        elif f == "Sprint":
            new = set(str(r.To_Value or "").replace(" ", "").split(",")) - {""}
            old = set(str(r.From_Value or "").replace(" ", "").split(",")) - {""}
            for s in new - old:
                add(f"{eid}+{s}", "add to sprint", r.Creation_Date, {}, base + [(sprjira2oid.get(s), "sprint")])
            for s in old - new:
                add(f"{eid}-{s}", "remove from sprint", r.Creation_Date, {}, base + [(sprjira2oid.get(s), "sprint")])
        elif f == "Component":
            if r.To_String:
                add(eid, "add component", r.Creation_Date, {"component": r.To_String},
                    base + [(compname2oid.get(r.To_String), "component")])
            elif r.From_String:
                add(eid, "remove component", r.Creation_Date, {"component": r.From_String},
                    base + [(compname2oid.get(r.From_String), "component")])
        elif f == "assignee":
            add(eid, "assign", r.Creation_Date, {},
                base + ([(user_oid(r.To_Value), "assignee")] if str(r.To_Value or "").isdigit() else []))
        elif f == "Story Points":
            add(eid, "estimate", r.Creation_Date, {"story_points": r.To_String}, base)

    for r in com.itertuples():
        au = (user_oid(r.Author_ID), "author") if pd.notna(r.Author_ID) else (None, None)
        add(f"c{r.ID}", "comment", r.Creation_Date, {"text": r.Comment_Text or ""}, [(r.issue, "issue"), au])

    # ---------------- final object-to-object relations ----------------
    o2o = []
    for r in lk[lk.Direction == "OUTBOUND"].itertuples():
        if r.src in keys and r.tgt in keys:
            o2o.append((r.src, r.tgt, r.Description.lower()))
    last = ev.sort_values("Creation_Date")
    epic = last[last.Field == "Epic Link"].groupby("issue")["To_String"].last()
    for i, e in epic.items():
        if e in keys:
            o2o.append((i, e, "part of epic"))
    par = last[last.Field.isin(["Parent", "Parent Issue"])].groupby("issue")["To_String"].last()
    for i, p in par.items():
        if p in keys:
            o2o.append((i, p, "sub-task of"))
    con = sqlite3.connect(TAWOS)
    fv = q(con, "select f.Issue_ID, f.Fix_Version_ID from Fix_Version f join Issue i on i.ID=f.Issue_ID "
                "where i.Project_ID=?", (pid,))
    ic = q(con, "select c.Issue_ID, c.Component_ID from Issue_Component c join Issue i on i.ID=c.Issue_ID "
                "where i.Project_ID=?", (pid,))
    con.close()
    for r in fv.itertuples():
        o2o.append((id2key[r.Issue_ID], f"release:{r.Fix_Version_ID}", "fix version"))
    for r in ic.itertuples():
        o2o.append((id2key[r.Issue_ID], f"component:{r.Component_ID}", "component"))
    for r in iss.itertuples():
        if pd.notna(r.Sprint_ID):
            o2o.append((r.oid, f"sprint:{int(r.Sprint_ID)}", "sprint"))
        if pd.notna(r.Assignee_ID):
            o2o.append((r.oid, user_oid(r.Assignee_ID), "assignee"))

    write(key, iss, ver, spr, comp, users, user_oid, events, e2o, o2o)


_INV = {}


def _inverse_desc(desc, lk):
    """Description of the opposite end of a link type, e.g. 'is blocked by' -> 'blocks'."""
    key = (id(lk), desc)
    if key not in _INV:
        _INV[key] = _inverse_desc_uncached(desc, lk)
    return _INV[key]


def _inverse_desc_uncached(desc, lk):
    m = lk[lk.Description.str.lower() == desc]
    if m.empty:
        return desc
    name, direction = m.iloc[0]["Name"], m.iloc[0]["Direction"]
    other = lk[(lk.Name == name) & (lk.Direction != direction)]
    return other.iloc[0]["Description"].lower() if not other.empty else desc


def write(key, iss, ver, spr, comp, users, user_oid, events, e2o, o2o):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{key}.sqlite"
    path.unlink(missing_ok=True)
    out = sqlite3.connect(path)
    type_map = lambda t: re.sub(r"\W+", "", t.title())

    # objects
    objs = [(o, "issue") for o in iss["oid"]] + [(o, "release") for o in ver["oid"]] + \
           [(o, "sprint") for o in spr["oid"]] + [(o, "component") for o in comp["oid"]] + \
           [(user_oid(u), "user") for u in users]
    pd.DataFrame(objs, columns=["ocel_id", "ocel_type"]).to_sql("object", out, index=False)
    otypes = ["issue", "release", "sprint", "component", "user"]
    pd.DataFrame({"ocel_type": otypes, "ocel_type_map": [type_map(t) for t in otypes]}).to_sql(
        "object_map_type", out, index=False)
    o_issue = iss.rename(columns={"oid": "ocel_id", "Title": "title", "Description_Text": "description",
                                  "Type": "issue_type", "Priority": "priority", "Status": "status",
                                  "Resolution": "resolution", "Story_Point": "story_points",
                                  "Creation_Date": "ocel_time"})
    o_issue["ocel_changed_field"] = None
    o_issue[["ocel_id", "ocel_time", "ocel_changed_field", "title", "description", "issue_type",
             "priority", "status", "resolution", "story_points"]].to_sql(
        "object_Issue", out, index=False)
    v = ver.rename(columns={"oid": "ocel_id", "Name": "name", "Release_Date": "release_date",
                            "Released": "released"})
    v["ocel_time"] = None
    v["ocel_changed_field"] = None
    v[["ocel_id", "ocel_time", "ocel_changed_field", "name", "release_date", "released"]].to_sql(
        "object_Release", out, index=False)
    s = spr.rename(columns={"oid": "ocel_id", "Name": "name", "Start_Date": "start_date",
                            "End_Date": "end_date", "Complete_Date": "complete_date"})
    s["ocel_time"] = None
    s["ocel_changed_field"] = None
    s[["ocel_id", "ocel_time", "ocel_changed_field", "name", "start_date", "end_date",
       "complete_date"]].to_sql("object_Sprint", out, index=False)
    c = comp.rename(columns={"oid": "ocel_id", "Name": "name"})
    c["ocel_time"] = None
    c["ocel_changed_field"] = None
    c[["ocel_id", "ocel_time", "ocel_changed_field", "name"]].to_sql("object_Component", out, index=False)
    pd.DataFrame({"ocel_id": [user_oid(u) for u in users], "ocel_time": None,
                  "ocel_changed_field": None}).to_sql("object_User", out, index=False)

    # events
    edf = pd.DataFrame([(e, t) for e, t, _, _ in events], columns=["ocel_id", "ocel_type"])
    edf.to_sql("event", out, index=False)
    etypes = sorted(edf["ocel_type"].unique())
    pd.DataFrame({"ocel_type": etypes, "ocel_type_map": [type_map(t) for t in etypes]}).to_sql(
        "event_map_type", out, index=False)
    by_type = {}
    for e, t, time, attrs in events:
        by_type.setdefault(t, []).append({"ocel_id": e, "ocel_time": time, **attrs})
    for t, rows in by_type.items():
        pd.DataFrame(rows).to_sql(f"event_{type_map(t)}", out, index=False)
    pd.DataFrame(e2o, columns=["ocel_event_id", "ocel_object_id", "ocel_qualifier"]).to_sql(
        "event_object", out, index=False)
    pd.DataFrame(o2o, columns=["ocel_source_id", "ocel_target_id", "ocel_qualifier"]).drop_duplicates().to_sql(
        "object_object", out, index=False)
    out.execute("create index ix_eo_e on event_object(ocel_event_id)")
    out.execute("create index ix_eo_o on event_object(ocel_object_id)")
    out.commit()
    n_multi = pd.DataFrame(e2o, columns=["e", "o", "q"]).groupby("e")["o"].nunique()
    print(f"{key}: {len(objs)} objects, {len(events)} events "
          f"({(n_multi > 2).mean():.0%} with more than issue+author), {len(o2o)} O2O -> {path}")
    out.close()


if __name__ == "__main__":
    for k in sys.argv[1:] or ["MDL", "DM", "SERVER", "MESOS"]:
        export(k)
