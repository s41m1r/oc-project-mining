"""F5: do tasks with low predicted slack slip to later releases more often?

For each released version p with at least MIN_PLANNED planned tasks:
  - t0 = planning point: the first start of work on a task planned for p
    (variant "mid": halfway between t0 and the release date);
  - planned tasks = issues whose fix version was p at t0, reconstructed from the
    set/remove fix version events of the OCEL (relations change over time);
  - slipped = planned at t0 but finally not in p (moved to another release or none);
  - the network is mined as of t0: declared links present at t0, stated type or FF,
    no plausibility check (future times unknown), durations from tasks finished
    before t0 (Kaplan-Meier reference classes), predicted slack and criticality.

Writes results/f5/tasks.csv and results/f5/summary.csv.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from mining import (DAY, LINK_SEMANTICS, N_SAMPLES, cpm, cpm_structure, duration_model, lifecycles,
                    load_ocel, reference_classes, rel, sample)

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "results" / "f5"
PROJECTS = ["MDL", "SERVER", "MESOS"]
MIN_PLANNED = 20


def membership_history(L):
    """(issue, release) -> sorted list of (time, +1/-1) from fix version events."""
    ev = L.ev[L.ev.ocel_type.isin(["set fix version", "remove fix version"])]
    iss = ev[ev.ocel_qualifier == "issue"].set_index("ocel_event_id")["ocel_object_id"]
    relo = ev[ev.ocel_qualifier == "release"].set_index("ocel_event_id")["ocel_object_id"]
    meta = ev.drop_duplicates("ocel_event_id").set_index("ocel_event_id")[["ocel_type", "t"]]
    hist = {}
    for e in meta.index:
        if e in iss.index and e in relo.index:
            hist.setdefault((iss[e], relo[e]), []).append(
                (meta.at[e, "t"], 1 if meta.at[e, "ocel_type"] == "set fix version" else -1))
    for k in hist:
        hist[k].sort()
    return hist


def members_at(L, p, t, hist, created, final):
    """Issues whose fix version was p at time t."""
    out = set()
    cand = {i for (i, r) in hist if r == p} | final
    for i in cand:
        h = hist.get((i, p))
        if h:
            state = None
            for ts, s in h:
                if ts <= t:
                    state = s
            if state == 1 or (state is None and i in final and h[0][1] == -1 and created.get(i, t) <= t):
                out.add(i)                       # removed later, so it was a member before
        elif i in final and created.get(i, np.inf) <= t:
            out.add(i)                           # fix version set at creation, no event
    return out


def links_at(L, T, t):
    """Declared or typed dependency links between tasks of T present at time t."""
    Tset = set(T)
    lk = L.ev[L.ev.ocel_type.isin(["add link", "remove link"]) & (L.ev.t <= t)]
    src = lk[lk.ocel_qualifier == "source"].set_index("ocel_event_id")["ocel_object_id"]
    tgt = lk[lk.ocel_qualifier == "target"].set_index("ocel_event_id")["ocel_object_id"]
    meta = lk.drop_duplicates("ocel_event_id").set_index("ocel_event_id")[["ocel_type", "t"]].sort_values("t")
    desc = pd.concat([L.attrs[k]["description"] for k in ("add link", "remove link") if k in L.attrs])
    present = {}
    for e in meta.index:
        x, y = src.get(e), tgt.get(e)
        sem = LINK_SEMANTICS.get(desc.get(e))
        if x not in Tset or y not in Tset or x == y or sem is None:
            continue
        pair = (x, y) if sem[0] == "x" else (y, x)
        present[pair] = (meta.at[e, "ocel_type"] == "add link", sem[1] or "FF")
    edges = {p: {"type": tau} for p, (on, tau) in present.items() if on}
    # break cycles (lowest-information arcs first) to keep the relation acyclic
    import networkx as nx
    G = nx.DiGraph(list(edges))
    while True:
        try:
            cyc = nx.find_cycle(G)
        except nx.NetworkXNoCycle:
            break
        u, v = cyc[0][:2]
        G.remove_edge(u, v)
        edges.pop((u, v))
    return edges


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for key in PROJECTS:
        L = load_ocel(BASE / "data" / "ocel" / f"{key}.sqlite")
        alpha, omega, parent = lifecycles(L)
        hist = membership_history(L)
        created = dict(zip(L.ev[L.ev.ocel_type == "create issue"].ocel_object_id,
                           L.ev[L.ev.ocel_type == "create issue"].t))
        fv = rel(L, "fix version")
        final_of = {}
        for i, rs in fv.items():
            for r in rs:
                final_of.setdefault(r, set()).add(i)
        comp = rel(L, "component")
        released = L.releases[(L.releases.released == 1) & L.releases.release_date.notna()]
        for p, row in released.iterrows():
            final = final_of.get(p, set())
            starts = [alpha[i] for i in final if i in alpha]
            if not starts:
                continue
            t_rel = pd.Timestamp(row.release_date).timestamp()
            for variant, t0 in (("start", min(starts)), ("mid", (min(starts) + t_rel) / 2)):
                planned = members_at(L, p, t0, hist, created, final)
                planned = {i for i in planned if i in L.issues.index and L.issues.at[i, "issue_type"] != "Epic"
                           and not (i in omega and omega[i] <= t0)}       # still open at t0
                if len(planned) < MIN_PLANNED:
                    continue
                T = sorted(planned)
                edges = links_at(L, T, t0)
                ref = reference_classes(L, alpha, omega, t0)
                if ref.obs.sum() < 10:
                    continue
                models = {i: duration_model(ref, L.issues.at[i, "issue_type"],
                                            (sorted(comp.get(i, [])) or [None])[0],
                                            L.issues.at[i, "story_points"]) for i in T}
                d50 = {i: models[i]["p50"] for i in T}
                structure = cpm_structure(T, edges)
                _, _, slack, end = cpm(T, edges, d50, structure)
                rng = np.random.default_rng(0)
                draws = {i: sample(models[i], rng, 200) for i in T}
                crit = dict.fromkeys(T, 0)
                for k in range(200):
                    _, _, sl, _ = cpm(T, edges, {i: draws[i][k] for i in T}, structure)
                    for i, v in sl.items():
                        crit[i] += v < 1e-9
                conn = {x for e in edges for x in e}
                for i in T:
                    rows.append({"project": key, "release": row["name"], "variant": variant, "task": i,
                                 "slipped": i not in final, "slack": slack[i], "p50": d50[i],
                                 "criticality": crit[i] / 200, "connected": i in conn,
                                 "n_succ": sum(1 for e in edges if e[0] == i),
                                 "n_pred": sum(1 for e in edges if e[1] == i)})
        print(key, "done", len(rows))
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "tasks.csv", index=False)
    summ = []
    for (key, variant), g in df.groupby(["project", "variant"]):
        g = g.copy()
        g["slack_q"] = g.groupby("release")["slack"].rank(pct=True)     # within-release quantile
        low = g[g.slack_q <= 0.25]
        high = g[g.slack_q > 0.75]
        summ.append({"project": key, "variant": variant, "releases": g.release.nunique(), "tasks": len(g),
                     "slip_rate": g.slipped.mean(),
                     "slip_low_slack_q1": low.slipped.mean(), "slip_high_slack_q4": high.slipped.mean(),
                     "slip_connected": g[g.connected].slipped.mean(),
                     "slip_isolated": g[~g.connected].slipped.mean(),
                     "slip_has_pred": g[g.n_pred > 0].slipped.mean(),
                     "slip_no_pred": g[g.n_pred == 0].slipped.mean(),
                     "slip_critical": g[g.criticality >= 0.5].slipped.mean(),
                     "n_critical": int((g.criticality >= 0.5).sum())})
    S = pd.DataFrame(summ)
    S.round(3).to_csv(OUT / "summary.csv", index=False)
    print(S.round(3).T.to_string())


if __name__ == "__main__":
    main()
