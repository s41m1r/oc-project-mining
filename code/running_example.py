"""Running example (Table 1 of the paper) as an OCEL, mined with the pipeline.

Writes data/ocel/EXAMPLE.sqlite and results/running_example.json.
Usage: python running_example.py [--llm]   (--llm queries E3 for the example pairs)
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import e3_llm
from mining import load_ocel, mine

BASE = Path(__file__).resolve().parents[1]
DB = BASE / "data" / "ocel" / "EXAMPLE.sqlite"
T0 = datetime(2026, 1, 5)
day = lambda d: (T0 + timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")

# Internal keys in tracker format (the paper shows them as A-F and epic X).
A, B, C, D, E, F, X = (f"PRJ-{n}" for n in range(1, 8))
LETTER = {A: "A", B: "B", C: "C", D: "D", E: "E", F: "F", X: "X"}
ISSUES = {  # key: (title, epic, started, resolved)
    A: ("Make test library compatible with new version", X, 2, 10),
    B: ("Fix invalid array operations", X, 1, 10),
    C: ("Backport library upgrade", X, 2, 10),
    D: ("Bump test framework version", X, 6, 15),
    E: ("Switch CI to new version", X, 0, 18),
    F: ("Fix regression in test setup", None, 12, 13),
}
LINKS = [  # (source, description, target, added, removed)
    (A, "blocks", E, 7, None), (B, "blocks", E, 7, None), (C, "blocks", E, 7, None),
    (A, "blocks", D, 8, None), (F, "is a regression caused by", A, 11, None),
    (F, "blocks", D, 12, 12),
]
COMMENTS = [(D, f"To test this with the new version, we need all issues of epic {X} to be applied.", 9),
            (D, f"Unblocking from {F}: both issues are unrelated and can follow their own process.", 12)]


def build():
    DB.unlink(missing_ok=True)
    con = sqlite3.connect(DB)
    keys = list(ISSUES) + [X]
    pd.DataFrame({"ocel_id": keys + ["release:R"], "ocel_type": ["issue"] * len(keys) + ["release"]}).to_sql(
        "object", con, index=False)
    pd.DataFrame({"ocel_id": keys, "ocel_time": day(-1), "ocel_changed_field": None,
                  "title": [ISSUES[k][0] for k in ISSUES] + ["Support the new language version"],
                  "description": "", "issue_type": ["Task"] * len(ISSUES) + ["Epic"],
                  "priority": None, "status": "Closed", "resolution": "Fixed",
                  "story_points": None}).to_sql("object_Issue", con, index=False)
    pd.DataFrame({"ocel_id": ["release:R"], "ocel_time": None, "ocel_changed_field": None,
                  "name": ["R"], "release_date": [day(20)], "released": [1]}).to_sql("object_Release", con, index=False)
    pd.DataFrame(columns=["ocel_id", "start_date"]).to_sql("object_Sprint", con, index=False)
    o2o = [(k, "release:R", "fix version") for k in ISSUES] + \
          [(k, X, "part of epic") for k, v in ISSUES.items() if v[1]] + \
          [(s, t, d) for s, d, t, _, r in LINKS if r is None]
    pd.DataFrame(o2o, columns=["ocel_source_id", "ocel_target_id", "ocel_qualifier"]).to_sql(
        "object_object", con, index=False)
    ev, eo, by = [], [], {}
    def add(eid, etype, t, attrs, objs):
        ev.append((eid, etype))
        by.setdefault(etype, []).append({"ocel_id": eid, "ocel_time": day(t), **attrs})
        eo.extend((eid, o, q) for o, q in objs)
    for k, (_, _, s, r) in ISSUES.items():
        add(f"start:{k}", "set status", s, {"status": "In Progress", "category": "in progress"}, [(k, "issue")])
        add(f"resolve:{k}", "resolve", r, {"resolution": "Fixed"}, [(k, "issue")])
    for n, (s, d, t, a, r) in enumerate(LINKS, 1):
        add(f"l{n}+", "add link", a, {"link_type": d, "description": d}, [(s, "source"), (t, "target")])
        if r is not None:
            add(f"l{n}-", "remove link", r, {"link_type": d, "description": d}, [(s, "source"), (t, "target")])
    for n, (k, text, t) in enumerate(COMMENTS, 1):
        add(f"c{n}", "comment", t, {"text": text}, [(k, "issue")])
    pd.DataFrame(ev, columns=["ocel_id", "ocel_type"]).to_sql("event", con, index=False)
    m = {t: t.title().replace(" ", "") for t in by}
    pd.DataFrame({"ocel_type": list(m), "ocel_type_map": list(m.values())}).to_sql("event_map_type", con, index=False)
    for t, rows in by.items():
        pd.DataFrame(rows).to_sql(f"event_{m[t]}", con, index=False)
    pd.DataFrame(eo, columns=["ocel_event_id", "ocel_object_id", "ocel_qualifier"]).to_sql(
        "event_object", con, index=False)
    con.commit()
    con.close()


def main(use_llm=False):
    build()
    L = load_ocel(DB)
    T = list(ISSUES)
    if use_llm:
        e3_llm.run(L, T, "EXAMPLE")
    e3 = e3_llm.as_evidence(e3_llm.load("EXAMPLE"))
    net = mine(L, "release:R", e3=e3, durations=False)
    from mining import realized_slack, lifecycles, DAY
    alpha, omega, _ = lifecycles(L)
    d = {i: (omega[i] - alpha[i]) / DAY for i in T}
    rs = realized_slack(T, net.edges, alpha, omega, d)
    out = {"edges": [{"pred": LETTER[i], "succ": LETTER[j], **{k: v for k, v in e.items() if k != "added"}}
                     for (i, j), e in net.edges.items()],
           "dropped": [{"pred": LETTER[p[0]], "succ": LETTER[p[1]], "reason": r, "type": e["type"]}
                       for p, r, e in net.dropped],
           "realized_slack_days": {LETTER[k]: v for k, v in rs.items()}}
    (BASE / "results" / "running_example.json").write_text(json.dumps(out, indent=1, default=str))
    for e in out["edges"]:
        print(f"{e['pred']} -> {e['succ']}  {e['type']}  support={e['support']:.2f}  "
              f"declared={e['declared']} hidden={e['hidden']}")
    for e in out["dropped"]:
        print(f"dropped {e['pred']} -> {e['succ']} ({e['reason']})")
    print("realized slack (days):", {k: round(v, 1) for k, v in out["realized_slack_days"].items()})


if __name__ == "__main__":
    main(use_llm="--llm" in sys.argv)
