"""Fig. 2: mined project network of the running example as a Gantt view.
Reads results/running_example.json; writes paper/figures/fig-example.pdf (results/figures/ if there is no paper folder)."""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("pdf")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch

sys.path.insert(0, str(Path(__file__).parent))
from running_example import ISSUES, LETTER

BASE = Path(__file__).resolve().parents[1]
OUT = (BASE.parent / "paper" / "figures" if (BASE.parent / "paper").exists()
       else BASE / "results" / "figures") / "fig-example.pdf"
INK, MUTED, BAR, CRIT = "#222222", "#888888", "#cfcfcf", "#4a4a4a"

plt.rcParams.update({"font.family": "serif", "font.size": 8, "axes.linewidth": 0.6,
                     "pdf.fonttype": 42})
res = json.loads((BASE / "results" / "running_example.json").read_text())
times = {LETTER[k]: (v[2], v[3]) for k, v in ISSUES.items()}
order = ["A", "B", "C", "D", "E", "F"]
y = {t: len(order) - 1 - n for n, t in enumerate(order)}
slack = res["realized_slack_days"]

fig, ax = plt.subplots(figsize=(4.75, 1.9))
H = 0.42
for t in order:
    s, f = times[t]
    crit = slack.get(t, 1) < 1e-9
    ax.barh(y[t], f - s, left=s, height=H, color=CRIT if crit else BAR, edgecolor=INK, linewidth=0.5)
    ax.text(19.3, y[t], f"{slack[t]:g}", va="center", ha="right", fontsize=7, color=INK)
for e in res["edges"]:
    i, j, tau = e["pred"], e["succ"], e["type"]
    xi = times[i][1]
    xj = times[j][1] if tau == "FF" else times[j][0]
    yi = y[i]                                      # leave the bar at its end, mid-height
    yj = y[j] + (-H / 2 if y[j] > y[i] else H / 2)
    rad = -0.3 if e["hidden"] else 0.0             # hidden precedences curve away from declared ones
    ax.add_patch(FancyArrowPatch((xi, yi), (xj, yj), arrowstyle="-|>", mutation_scale=7,
                                 lw=0.8, color=INK, linestyle=(0, (3, 2)) if e["hidden"] else "-",
                                 connectionstyle=f"arc3,rad={rad}", shrinkA=0, shrinkB=0, zorder=3))
end = max(f for _, f in times.values())
ax.axvline(end, color=MUTED, lw=0.6, ls=":")
ax.text(end - 0.2, -0.6, "release end", color=MUTED, fontsize=6.5, va="bottom", ha="right")
ax.text(19.3, len(order) - 0.55, "slack (d)", fontsize=6.5, color=MUTED, ha="right", va="bottom")
ax.set_yticks([y[t] for t in order], order)
ax.set_xlim(-0.5, 19.6)
ax.set_ylim(-0.7, len(order) - 0.15)
ax.set_xlabel("day")
ax.spines[["top", "right"]].set_visible(False)
ax.tick_params(length=2)
# legend as text: arrow ends encode the type
from matplotlib.lines import Line2D
handles = [Line2D([], [], color=INK, lw=0.8, label="declared"),
           Line2D([], [], color=INK, lw=0.8, ls=(0, (3, 2)), label="hidden (text only)"),
           plt.Rectangle((0, 0), 1, 1, fc=CRIT, ec=INK, lw=0.5, label="critical task")]
ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.0), fontsize=6.5, frameon=False,
          ncol=3, handlelength=2.2, columnspacing=1.5)
ax.text(0.0, -0.6, "FF: finish-to-finish arrow, FS: finish-to-start arrow", fontsize=6, color=MUTED,
        va="bottom")
fig.tight_layout(pad=0.2)
OUT.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(OUT)
print("wrote", OUT)
