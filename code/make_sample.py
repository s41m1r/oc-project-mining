"""Cut a small OCEL for one release out of a full tracker-project OCEL, so the
pipeline can be tried without downloading TAWOS.

Kept: the tasks of the release with all their events (status, links, comments,
...) and related objects; the earlier issues that Step 5 needs as reference
classes (started before the release's first task), with their status and
resolution events only. Mining the sample gives the same network and the same
durations as mining the full OCEL.

Usage: python make_sample.py MESOS 1.0.0   ->  data-sample/MESOS-1.0.0.sqlite
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from mining import lifecycles, load_ocel, tasks_of

BASE = Path(__file__).resolve().parents[1]


def main(key, release):
    src = BASE / "data" / "ocel" / f"{key}.sqlite"
    out = BASE / "data-sample" / f"{key}-{release}.sqlite"
    L = load_ocel(src)
    alpha, omega, parent = lifecycles(L)
    rid = L.releases.index[L.releases.name == release][0]
    T, _ = tasks_of(L, rid, parent)
    members = set(L.o2o[(L.o2o.ocel_qualifier == "fix version") & (L.o2o.ocel_target_id == rid)].ocel_source_id)
    full = members | {s for s, p in parent.items() if p in members}     # tasks, incl. sub-tasks and dropped ones
    cutoff = min(alpha[i] for i in T if i in alpha)
    ref = {i for i, a in alpha.items() if a < cutoff} - full              # reference classes (Step 5)

    con = sqlite3.connect(src)
    rd = lambda q, *p: pd.read_sql_query(q, con, params=p)
    eo = rd("select * from event_object")
    ev = rd("select * from event")
    etype = dict(zip(ev.ocel_id, ev.ocel_type))
    eo["type"] = eo.ocel_event_id.map(etype)
    keep_e = set(eo[eo.ocel_object_id.isin(full)].ocel_event_id) | \
        set(eo[eo.ocel_object_id.isin(ref) & eo.type.isin(["set status", "resolve", "create issue"])].ocel_event_id)
    epics = set(L.o2o[(L.o2o.ocel_qualifier == "part of epic") & L.o2o.ocel_source_id.isin(full)].ocel_target_id)
    eo = eo[eo.ocel_event_id.isin(keep_e)]
    related = set(L.o2o[L.o2o.ocel_source_id.isin(full | ref)].ocel_target_id)   # components, sprints, ...
    objs = set(eo.ocel_object_id) | full | ref | epics | related | {rid}
    eo = eo[eo.ocel_object_id.isin(objs)].drop(columns="type")

    out.parent.mkdir(exist_ok=True)
    out.unlink(missing_ok=True)
    dst = sqlite3.connect(out)
    obj = rd("select * from object")
    obj[obj.ocel_id.isin(objs)].to_sql("object", dst, index=False)
    rd("select * from object_map_type").to_sql("object_map_type", dst, index=False)
    for (m,) in con.execute("select ocel_type_map from object_map_type"):
        df = rd(f"select * from object_{m}")
        df = df[df.ocel_id.isin(objs)]
        if m == "Issue":   # texts only for the tasks of the release and their epics
            df.loc[~df.ocel_id.isin(full | epics), "description"] = None
        df.to_sql(f"object_{m}", dst, index=False)
    ev[ev.ocel_id.isin(keep_e)].to_sql("event", dst, index=False)
    emap = rd("select * from event_map_type")
    emap.to_sql("event_map_type", dst, index=False)
    for m in emap.ocel_type_map:
        df = rd(f"select * from event_{m}")
        df[df.ocel_id.isin(keep_e)].to_sql(f"event_{m}", dst, index=False)
    eo.to_sql("event_object", dst, index=False)
    oo = rd("select * from object_object")
    oo[oo.ocel_source_id.isin(objs) & oo.ocel_target_id.isin(objs)].to_sql("object_object", dst, index=False)
    dst.execute("create index ix_eo_e on event_object(ocel_event_id)")
    dst.execute("create index ix_eo_o on event_object(ocel_object_id)")
    dst.commit()
    dst.execute("vacuum")
    dst.close()
    print(f"{out}: {len(full)} tasks, {len(ref)} reference issues, {len(keep_e)} events, "
          f"{out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(*(sys.argv[1:3] if len(sys.argv) > 2 else ("MESOS", "1.0.0")))
