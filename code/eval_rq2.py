"""RQ2: what do mined project networks reveal about how real releases unfolded?

For each tracker project (MDL, SERVER, MESOS), mines the network of every
release with at least MIN_LINKS declared dependency links among its tasks, and
writes edge- and task-level records plus a summary:

  results/rq2/edges.csv, results/rq2/tasks.csv, results/rq2/releases.csv,
  results/rq2/summary.csv

Usage: python eval_rq2.py [--llm KEY:RELEASE ...]   (E3 for selected releases)
"""
import sys
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import e3_llm
from mining import (DAY, LINK_SEMANTICS, link_evidence, lifecycles, load_ocel, mine, satisfied,
                    tasks_of)

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "results" / "rq2"
PROJECTS = ["MDL", "SERVER", "MESOS"]
MIN_LINKS = 10


def longest_chain(T, edges):
    G = nx.DiGraph(list(edges))
    return nx.dag_longest_path_length(G) + 1 if G.number_of_edges() else (1 if T else 0)


def main(llm_targets=()):
    OUT.mkdir(parents=True, exist_ok=True)
    edges_rows, task_rows, rel_rows = [], [], []
    for key in PROJECTS:
        L = load_ocel(BASE / "data" / "ocel" / f"{key}.sqlite")
        lc = lifecycles(L)
        alpha, omega, parent = lc
        released = L.releases[(L.releases.released == 1) & L.releases.release_date.notna()]
        for rid, rname in released["name"].items():
            T, _ = tasks_of(L, rid, parent)
            if len(T) < 2:
                continue
            e1 = link_evidence(L, T)
            declared = {p: d for p, d in e1.items() if d["kind"] == "declared"}
            if sum(d["present"] for d in declared.values()) < MIN_LINKS:
                continue
            name = f"{key}-{rid}"
            if f"{key}:{rname}" in llm_targets:
                e3_llm.run(L, T, name)
            answers = e3_llm.load(name)
            e3 = e3_llm.as_evidence(answers)
            net = mine(L, rid, e3=e3, lc=lc)
            # every declared link, before plausibility filtering (F1-F3)
            for (i, j), d in declared.items():
                edges_rows.append({
                    "project": key, "release": rname, "pred": i, "succ": j, "source": "declared",
                    "present": d["present"], "removed": d["n_removed"] > 0,
                    "fs_ok": satisfied("FS", i, j, alpha, omega), "ff_ok": satisfied("FF", i, j, alpha, omega),
                    "added_after_succ_start": (d["added"] is not None and j in alpha and d["added"] > alpha[j]),
                    "added_after_pred_end": (d["added"] is not None and i in omega and d["added"] > omega[i]),
                    "in_network": (i, j) in net.edges,
                    "type": net.edges.get((i, j), {}).get("type"),
                })
            for (i, j), d in net.edges.items():
                if not d["declared"]:
                    edges_rows.append({"project": key, "release": rname, "pred": i, "succ": j,
                                       "source": "hidden" if d["hidden"] else "other",
                                       "present": False, "in_network": True, "type": d["type"],
                                       "quote": d["quote"]})
            t = net.tasks.copy()
            t["project"], t["release"] = key, rname
            conn = {x for p in net.edges for x in p}
            t["connected"] = t.task.isin(conn)
            task_rows.append(t)
            rel_rows.append({"project": key, "release": rname, "tasks": len(T),
                             "no_start": int(t.alpha.isna().sum()),
                             "edges": len(net.edges), "connected": len(conn),
                             "FS": sum(e["type"] == "FS" for e in net.edges.values()),
                             "FF": sum(e["type"] == "FF" for e in net.edges.values()),
                             "hidden": sum(e["hidden"] for e in net.edges.values()),
                             "e3_pairs": len(answers),
                             "dropped_violated": sum(r == "violated" for _, r, _ in net.dropped),
                             "dropped_support": sum(r == "support" for _, r, _ in net.dropped),
                             "dropped_transitive": sum(r == "transitive" for _, r, _ in net.dropped),
                             "longest_chain": longest_chain(T, net.edges)})
            print(f"{key} {rname}: {rel_rows[-1]}")
    E = pd.DataFrame(edges_rows)
    Tk = pd.concat(task_rows)
    R = pd.DataFrame(rel_rows)
    E.to_csv(OUT / "edges.csv", index=False)
    Tk.to_csv(OUT / "tasks.csv", index=False)
    R.to_csv(OUT / "releases.csv", index=False)

    # ---------------- summary per tracker project ----------------
    rows = []
    for key, g in E[E.source == "declared"].groupby("project"):
        pres = g[g.present]
        dec = pres[pres.ff_ok.notna()]
        conn = Tk[(Tk.project == key) & Tk.connected & Tk.slack.notna() & Tk.realized_slack.notna()]
        rho = conn[["slack", "realized_slack"]].corr(method="spearman").iloc[0, 1] if len(conn) > 2 else np.nan
        rr = R[R.project == key]
        rows.append({
            "project": key, "releases": len(rr), "tasks": rr.tasks.sum(),
            "declared_links": len(pres),
            "fs_satisfied": (dec.fs_ok == True).mean(),
            "ff_satisfied": (dec.ff_ok == True).mean(),
            "violated_ff": (dec.ff_ok == False).mean(),
            "added_after_succ_start": pres.added_after_succ_start.mean(),
            "added_after_pred_end": pres.added_after_pred_end.mean(),
            "removed_share": g.removed.mean(),
            "network_edges": rr.edges.sum(), "FS": rr.FS.sum(), "FF": rr.FF.sum(), "hidden": rr.hidden.sum(),
            "connected_share": rr.connected.sum() / rr.tasks.sum(),
            "median_longest_chain": rr.longest_chain.median(), "max_longest_chain": rr.longest_chain.max(),
            "spearman_pred_vs_realized_slack": rho,
        })
    S = pd.DataFrame(rows)
    S.round(3).to_csv(OUT / "summary.csv", index=False)
    print(S.round(3).T.to_string())


if __name__ == "__main__":
    targets = [a for a in sys.argv[1:] if ":" in a]
    main(llm_targets=targets)
