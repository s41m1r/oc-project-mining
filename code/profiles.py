"""Fig. 3 and validation sample for RQ2: evidence profile of the mined networks of
the releases with E3 (MDL 2.5, SERVER 4.3.1, MESOS 1.0.0).
Writes results/rq2/profiles.csv, results/rq2/hidden_sample.csv (for manual
validation) and paper/figures/fig-profiles.pdf (results/figures/ if there is no paper folder)."""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("pdf")
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import e3_llm
from mining import lifecycles, load_ocel, mine

BASE = Path(__file__).resolve().parents[1]
TARGETS = [("MDL", "2.5"), ("SERVER", "4.3.1"), ("MESOS", "1.0.0")]
rows, hidden = [], []
for key, name in TARGETS:
    L = load_ocel(BASE / "data" / "ocel" / f"{key}.sqlite")
    lc = lifecycles(L)
    rid = L.releases.index[L.releases.name == name][0]
    e3 = e3_llm.as_evidence(e3_llm.load(f"{key}-{rid}"))
    net = mine(L, rid, e3=e3, lc=lc, durations=False)
    for (i, j), e in net.edges.items():
        cat = "hidden" if e["hidden"] else ("declared + text" if e["textual"] else "declared only")
        rows.append({"project": key, "release": name, "pred": i, "succ": j, "category": cat,
                     "structural": bool(e["e2"]), "type": e["type"]})
        if e["hidden"]:
            hidden.append({"project": key, "release": name, "pred": i, "succ": j, "type": e["type"],
                           "quote": e["quote"], "valid (1/0)": ""})
P = pd.DataFrame(rows)
P.to_csv(BASE / "results" / "rq2" / "profiles.csv", index=False)
sample_path = BASE / "results" / "rq2" / "hidden_sample.csv"
if not sample_path.exists():          # never overwrite the sample once it has been labelled
    pd.DataFrame(hidden).sample(30, random_state=7).to_csv(sample_path, index=False)
share = P.groupby(["project", "category"]).size().unstack(fill_value=0)
share = share.div(share.sum(axis=1), axis=0)[["declared only", "declared + text", "hidden"]]
struct = P.groupby("project").structural.mean()
print(share.round(2)); print("with structural support:", struct.round(2).to_dict())

plt.rcParams.update({"font.family": "serif", "font.size": 8, "pdf.fonttype": 42, "axes.linewidth": 0.6})
fig, ax = plt.subplots(figsize=(4.75, 1.25))
order = ["MDL", "SERVER", "MESOS"]
labels = {k: f"{k} {n}" for k, n in TARGETS}
fills = {"declared only": ("#d9d9d9", None), "declared + text": ("#9a9a9a", None), "hidden": ("white", "////")}
for y, k in enumerate(reversed(order)):
    left = 0
    for c in share.columns:
        w = share.loc[k, c]
        fc, hatch = fills[c]
        ax.barh(y, w, left=left, height=0.6, color=fc, hatch=hatch, edgecolor="#222222", linewidth=0.5,
                label=c if y == 0 else None)
        if w >= 0.08:
            ax.text(left + w / 2, y, f"{w:.0%}", ha="center", va="center", fontsize=7,
                    bbox=dict(boxstyle="square,pad=0.1", fc="white", ec="none") if c == "hidden" else None)
        left += w
    n = int((P.project == k).sum())
    ax.text(1.01, y, f"n = {n}", va="center", fontsize=7)
ax.set_yticks(range(len(order)), [labels[k] for k in reversed(order)])
ax.set_xlim(0, 1)
ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
ax.spines[["top", "right"]].set_visible(False)
ax.tick_params(length=2)
ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=3, frameon=False, fontsize=7)
fig.tight_layout(pad=0.2)
out = (BASE.parent / "paper" / "figures" if (BASE.parent / "paper").exists()
       else BASE / "results" / "figures") / "fig-profiles.pdf"
fig.savefig(out)
print("wrote", out)
