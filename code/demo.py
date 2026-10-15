"""Mine the project network of a real release from the sample OCEL and print a
short report. Runs without downloading TAWOS and without an LLM key (textual
evidence comes from the cached LLM answers in results/e3/).

Usage: python demo.py        (writes results/demo/network.csv and tasks.csv)
"""
import sys
from pathlib import Path

import networkx as nx
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import e3_llm
from mining import load_ocel, mine

BASE = Path(__file__).resolve().parents[1]
OCEL, PROJECT, NAME = BASE / "data-sample" / "MESOS-1.0.0.sqlite", "release:414", "MESOS-release:414"


def main():
    L = load_ocel(OCEL)
    title = L.issues["title"].fillna("").str.strip('"')
    e3 = e3_llm.as_evidence(e3_llm.load(NAME))
    net = mine(L, PROJECT, e3=e3)
    T, E = net.tasks, net.edges
    out = BASE / "results" / "demo"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"pred": i, "succ": j, **{k: v for k, v in e.items() if k != "e2"},
                   "structural": ", ".join(e["e2"])} for (i, j), e in E.items()]).to_csv(out / "network.csv", index=False)
    T.to_csv(out / "tasks.csv", index=False)

    types = pd.Series([e["type"] for e in E.values()]).value_counts().to_dict()
    hidden = [(p, e) for p, e in E.items() if e["hidden"]]
    dropped = pd.Series([r for _, r, _ in net.dropped]).value_counts().to_dict()
    G = nx.DiGraph(list(E))
    chain = nx.dag_longest_path(G)
    print(f"Release MESOS 1.0.0: {len(T)} tasks, {int(T.alpha.isna().sum())} without a recorded start")
    print(f"Mined precedences: {len(E)} ({', '.join(f'{v} {k}' for k, v in types.items())}), "
          f"{len(hidden)} hidden (stated only in text)")
    print(f"Discarded candidates: {dropped}")
    print(f"Tasks in the network: {G.number_of_nodes()}, longest chain: {len(chain)} tasks")
    print("\nLongest chain:")
    for i in chain:
        print(f"  {i:12s} {title.get(i, '')[:70]}")
    print("\nHidden dependencies (first 5):")
    for (i, j), e in hidden[:5]:
        print(f"  {i} -> {j} [{e['type']}]  \"{e['quote'][:100]}\"")
    print("\nMost critical tasks (share of 1,000 duration samples in which the task is critical):")
    top = T.sort_values("criticality", ascending=False).head(5)
    for r in top.itertuples():
        print(f"  {r.task:12s} {r.criticality:5.0%}  median duration {r.p50:5.1f} d  {title.get(r.task, '')[:50]}")
    print(f"\nWritten: {(out / 'network.csv').relative_to(BASE)}, {(out / 'tasks.csv').relative_to(BASE)}")


if __name__ == "__main__":
    main()
