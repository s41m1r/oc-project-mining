"""RQ1: how accurately are dependencies and their types recovered, and how much
does LLM-extracted evidence (E3) contribute?

Ground truth: typed precedence links (Gantt and FS/FF/SS dependency link types)
between tasks of DM epics with at least 3 such links. These links are held out
from the input; the method sees all other records of the OCEL.

Variants:
  B1    declared links only (E1)
  E3    textual evidence only
  FULL  E1 + E2 + E3 (object-centric project mining)
  B2    LLM only: the epic's backlog in, network out (one request per epic)

Metrics: precision/recall/F1 of directed edges, strict and closure-aware
(an edge counts if it is implied by the transitive closure of the other side),
and type accuracy on matched edges whose ground-truth type is FS, FF, or SS.

Usage: python eval_rq1.py [--llm]     (--llm queries missing E3/B2 answers)
"""
import json
import sys
from pathlib import Path

import networkx as nx
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import e3_llm
from mining import load_ocel, lifecycles, link_evidence, mine, rel, tasks_of

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "results" / "rq1"
MIN_GT = 3


def epics_with_ground_truth(L, typed):
    epic = {s: t[0] for s, t in rel(L, "part of epic").items()}
    cnt = pd.Series([epic.get(i) for (i, j) in typed if epic.get(i) and epic.get(i) == epic.get(j)]).value_counts()
    return list(cnt[cnt >= MIN_GT].index)


def closure(edges):
    return set(nx.transitive_closure(nx.DiGraph(list(edges)), reflexive=False).edges()) if edges else set()


def score(pred, gt, gt_type, pred_type):
    """Directed-edge scores; pred/gt are sets of (i, j)."""
    tp_strict = len(pred & gt)
    cg, cp = closure(gt), closure(pred)
    tp_p = len({e for e in pred if e in cg})          # predicted edges implied by the ground truth
    tp_r = len({e for e in gt if e in cp})            # ground-truth edges implied by the prediction
    typed = [e for e in pred & gt if gt_type[e] in ("FS", "FF", "SS")]
    return {"pred": len(pred), "gt": len(gt), "tp": tp_strict, "tp_closure_p": tp_p, "tp_closure_r": tp_r,
            "typed_matches": len(typed), "type_correct": sum(pred_type[e] == gt_type[e] for e in typed)}


def main(use_llm=False):
    OUT.mkdir(parents=True, exist_ok=True)
    L = load_ocel(BASE / "data" / "ocel" / "DM.sqlite")
    lc = lifecycles(L)
    e1all = link_evidence(L, list(L.issues.index))
    typed = {p: d for p, d in e1all.items() if d["kind"] == "typed" and d["present"]}
    rows, edges_out = [], []
    for p in epics_with_ground_truth(L, typed):
        T, _ = tasks_of(L, p, lc[2])
        gt = {e for e in typed if e[0] in T and e[1] in T}
        if not gt:
            continue
        gt_type = {e: typed[e]["stated"] for e in gt}
        name = f"DM-{p}"
        deps = e3_llm.propose(L, T, name, use_llm=use_llm)             # also baseline B2
        extra = e3_llm.proposed_pairs(deps, T)
        if use_llm:
            e3_llm.run(L, T, name, extra_pairs=extra)
        answers = e3_llm.load(name)
        mention = set(e3_llm.candidate_pairs(e3_llm.task_texts(L, T)))
        e3_m = e3_llm.as_evidence(answers, only=mention)                 # mention-based candidates
        e3_h = e3_llm.as_evidence(answers, only=mention | set(extra))    # hybrid candidates
        variants = {
            "B1": mine(L, p, e3=None, include_typed=False, lc=lc, durations=False),
            "E3-mention": mine(L, p, e3=e3_m, include_typed=False, lc=lc, durations=False, e1_off=True),
            "FULL-mention": mine(L, p, e3=e3_m, include_typed=False, lc=lc, durations=False),
            "E3": mine(L, p, e3=e3_h, include_typed=False, lc=lc, durations=False, e1_off=True),
            "FULL": mine(L, p, e3=e3_h, include_typed=False, lc=lc, durations=False),
        }
        for v, net in variants.items():
            pred = set(net.edges)
            rows.append({"epic": p, "variant": v, "tasks": len(T),
                         **score(pred, gt, gt_type, {e: d["type"] for e, d in net.edges.items()})})
            for e, d in net.edges.items():
                edges_out.append({"epic": p, "variant": v, "pred": e[0], "succ": e[1], "type": d["type"],
                                  "support": d["support"], "hidden": d["hidden"], "in_gt": e in gt,
                                  "gt_type": gt_type.get(e), "quote": d["quote"]})
        if deps is not None:
            pred = {(d["predecessor"], d["successor"]) for d in deps
                    if d["predecessor"] in T and d["successor"] in T and d["predecessor"] != d["successor"]}
            ptype = {(d["predecessor"], d["successor"]): d["type"] for d in deps}
            rows.append({"epic": p, "variant": "B2", "tasks": len(T), **score(pred, gt, gt_type, ptype)})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "per_epic.csv", index=False)
    pd.DataFrame(edges_out).to_csv(OUT / "edges.csv", index=False)
    agg = df.groupby("variant")[["pred", "gt", "tp", "tp_closure_p", "tp_closure_r",
                                 "typed_matches", "type_correct"]].sum()
    agg["P"] = agg["tp"] / agg["pred"]
    agg["R"] = agg["tp"] / agg["gt"]
    agg["F1"] = 2 * agg["P"] * agg["R"] / (agg["P"] + agg["R"])
    agg["P_closure"] = agg["tp_closure_p"] / agg["pred"]
    agg["R_closure"] = agg["tp_closure_r"] / agg["gt"]
    agg["type_acc"] = agg["type_correct"] / agg["typed_matches"]
    agg.round(3).to_csv(OUT / "summary.csv")
    print(f"epics: {df.epic.nunique()}, tasks: {df.drop_duplicates('epic').tasks.sum()}")
    print(agg.round(3).to_string())


if __name__ == "__main__":
    main(use_llm="--llm" in sys.argv)
