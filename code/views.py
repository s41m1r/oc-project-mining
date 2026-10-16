"""Views of an OCEL and of a mined project network, shared by the notebook and the
command-line tool: the object-centric directly-follows graph (process mining) and
the PERT and Gantt charts of a project network (project mining)."""
import sqlite3
from dataclasses import replace
from textwrap import shorten, wrap

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

from mining import DAY, realized_slack

INK, MUTED, BAR, CRIT, NODE = "#222222", "#888888", "#d9d9d9", "#4a4a4a", "#f2f2f2"
DASH = (0, (3, 2))
REASONS = {"support": "support not above θ", "violated": "contradicts the observed times",
           "cycle": "weakest precedence in a cycle", "transitive": "implied by a path (transitive reduction)"}


# ----------------------------------------------------------------------------
# The OCEL
# ----------------------------------------------------------------------------
def ocel_overview(path):
    """Objects per type, events per type, and the share of events that involve at
    least two objects other than users."""
    con = sqlite3.connect(path)
    objects = pd.read_sql_query("select ocel_type as type, count(*) as objects from object "
                                "group by 1 order by 2 desc", con)
    events = pd.read_sql_query("select ocel_type as type, count(*) as events from event "
                               "group by 1 order by 2 desc", con)
    multi = pd.read_sql_query(
        "select count(*) from (select eo.ocel_event_id from event_object eo "
        "join object o on o.ocel_id = eo.ocel_object_id where o.ocel_type != 'user' "
        "group by eo.ocel_event_id having count(*) >= 2)", con).iloc[0, 0]
    con.close()
    return objects, events, multi / events.events.sum()


def event_table(L, objects=None, labels=None, t0=None):
    """One row per event: time (days since t0, or the timestamp), activity, the
    objects involved with their qualifiers, and the link type or comment text."""
    lab = labels or {}
    ev = activity_log(L, objects)
    detail = {}
    for df in L.attrs.values():
        col = next((c for c in ("description", "text") if c in df), None)
        if col:
            detail.update(df[col].dropna().astype(str).to_dict())
    t0s = None if t0 is None else pd.Timestamp(t0).as_unit("ns").value / 1e9
    rows = [{"day" if t0s is not None else "time":
             round((g.t.iat[0] - t0s) / DAY, 1) if t0s is not None else g.ocel_time.iat[0],
             "activity": g.activity.iat[0],
             "objects": ", ".join(f"{lab.get(o, o)} ({q})" for o, q in zip(g.ocel_object_id, g.ocel_qualifier)),
             "detail": detail.get(eid, "")}
            for eid, g in ev.groupby("ocel_event_id", sort=False)]
    return pd.DataFrame(rows).sort_values(list(rows[0])[:2], kind="stable", ignore_index=True)


def swap_objects(L, a, b):
    """The OCEL with the identities of objects a and b exchanged."""
    m = {a: b, b: a}
    sw = lambda s: s.map(lambda x: m.get(x, x))
    return replace(L, ev=L.ev.assign(ocel_object_id=sw(L.ev.ocel_object_id)),
                   o2o=L.o2o.assign(ocel_source_id=sw(L.o2o.ocel_source_id),
                                    ocel_target_id=sw(L.o2o.ocel_target_id)),
                   issues=L.issues.rename(index=m))


# ----------------------------------------------------------------------------
# Object-centric process mining
# ----------------------------------------------------------------------------
def activity_log(L, objects=None):
    """Event-object pairs with an activity label, ordered by object and time. The
    activity is the event type, or the new status for status changes."""
    ev = (L.ev if objects is None else L.ev[L.ev.ocel_object_id.isin(set(objects))]).copy()
    status = L.attrs["set status"]["status"] if "set status" in L.attrs else pd.Series(dtype=object)
    ev["activity"] = ev["ocel_type"]
    st = ev["ocel_type"] == "set status"
    ev.loc[st, "activity"] = ev.loc[st, "ocel_event_id"].map(status).fillna("set status")
    return ev.sort_values(["ocel_object_id", "t", "ocel_event_id"]).reset_index(drop=True)


def ocdfg(L, objects):
    """Directly-follows graph over the traces of the given objects (Def. 2 of the
    paper), with the frequency of each activity and arc. 'start' and 'end' mark the
    first and last activity of each trace. Returns (nodes, arcs)."""
    ev = activity_log(L, objects)
    g = ev.groupby("ocel_object_id")["activity"]
    steps = pd.DataFrame({"source": ev["activity"], "target": g.shift(-1)}).dropna()
    first = pd.DataFrame({"source": "start", "target": g.first()})
    last = pd.DataFrame({"source": g.last(), "target": "end"})
    arcs = (pd.concat([first, steps, last]).value_counts().rename("frequency").reset_index()
            .sort_values(["frequency", "source", "target"], ascending=[False, True, True],
                         ignore_index=True))
    rank = g.cumcount() / (g.transform("size").clip(lower=2) - 1)
    nodes = (pd.DataFrame({"activity": ev["activity"], "position": rank})
             .groupby("activity").agg(frequency=("position", "size"), position=("position", "mean")))
    n = ev["ocel_object_id"].nunique()
    nodes.loc["start"] = [n, -1.0]
    nodes.loc["end"] = [n, 2.0]
    nodes["frequency"] = nodes["frequency"].astype(int)
    return nodes.sort_values("position"), arcs


def plot_dfg(nodes, arcs, max_arcs=None, ax=None, lanes=None):
    """Draw a directly-follows graph from left to right, ordered by the mean
    relative position of each activity in the traces. With max_arcs, only the most
    frequent arcs are drawn. Self-loops are given as ↻ counts in the nodes."""
    shown = arcs if max_arcs is None else arcs.head(max_arcs)
    loops = shown[shown.source == shown.target].set_index("source")["frequency"]
    shown = shown[shown.source != shown.target]
    used = set(shown.source) | set(shown.target) | set(loops.index)
    acts = [a for a in nodes.index if a in used]
    lanes = lanes or (3 if len(acts) <= 10 else 5)
    seq = [0, 1, -1, 2, -2][:lanes]
    pos = {a: (k, 0 if a in ("start", "end") else seq[k % lanes]) for k, a in enumerate(acts)}
    if ax is None:
        _, ax = plt.subplots(figsize=(min(16, max(6, 1.25 * len(acts))), 1.0 + 0.9 * lanes))
    fmax = shown.frequency.max() if len(shown) else 1
    boxes = {}
    for a, (x, y) in pos.items():
        terminal = a in ("start", "end")
        text = a if terminal else f"{a}\n{nodes.at[a, 'frequency']}" + (
            f" · ↻{loops[a]}" if a in loops.index else "")
        boxes[a] = ax.text(x, y, text, ha="center", va="center", fontsize=7.5, color=INK, zorder=3,
                           bbox=dict(boxstyle="circle,pad=0.3" if terminal else "round,pad=0.35",
                                     fc="white" if terminal else NODE, ec=INK, lw=0.6))
    ax.figure.canvas.draw()                       # box sizes are known only after drawing
    for r in shown.itertuples():
        lw = 0.4 + 2.2 * r.frequency / fmax
        p = FancyArrowPatch(pos[r.source], pos[r.target], arrowstyle="-|>", mutation_scale=9 + 4 * lw,
                            lw=lw, color=INK, alpha=0.75, zorder=2,
                            connectionstyle="arc3,rad=0.25",
                            patchA=boxes[r.source].get_bbox_patch(),
                            patchB=boxes[r.target].get_bbox_patch())
        ax.add_patch(p)
        if len(shown) <= 25:
            (x0, y0), (x1, y1) = pos[r.source], pos[r.target]
            mx, my = (x0 + x1) / 2 + 0.12 * (y1 - y0), (y0 + y1) / 2 - 0.12 * (x1 - x0)
            ax.text(mx, my, str(r.frequency), fontsize=6.5, color=MUTED, ha="center", va="center",
                    zorder=4, bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none"))
    ax.set_xlim(-0.7, len(acts) - 0.3)
    ax.set_ylim(min(seq) - 0.8, max(seq) + 0.8)
    ax.axis("off")
    return ax


# ----------------------------------------------------------------------------
# Object-centric project mining
# ----------------------------------------------------------------------------
def evidence(e):
    if e["declared"]:
        return "declared + text" if e["textual"] else "declared"
    return "text only (hidden)" if e["textual"] else "structural"


def precedence_table(net, labels=None):
    """One row per precedence of the network, with its type, support, and evidence."""
    lab = labels or {}
    return pd.DataFrame([{"predecessor": lab.get(i, i), "successor": lab.get(j, j), "type": e["type"],
                          "stated type": e["stated"] or "", "support": round(e["support"], 2),
                          "evidence": evidence(e), "structural relations": ", ".join(e["e2"]),
                          "quote": e["quote"] or ""}
                         for (i, j), e in net.edges.items()])


def dropped_table(net, labels=None):
    """One row per discarded candidate, with the reason."""
    lab = labels or {}
    return pd.DataFrame([{"predecessor": lab.get(i, i), "successor": lab.get(j, j),
                          "reason": REASONS.get(r, r), "type": e["type"], "support": round(e["support"], 2),
                          "evidence": evidence(e) if not e["removed"] else "removed link"}
                         for (i, j), r, e in net.dropped])


def subnetwork(net, tasks=None):
    """The given tasks (default: all tasks in some precedence) and the precedences
    among them."""
    if tasks is None:
        tasks = sorted({i for p in net.edges for i in p})
    T = list(tasks)
    keep = set(T)
    return T, {p: e for p, e in net.edges.items() if p[0] in keep and p[1] in keep}


def realized_times(net, T, E):
    """Observed start, finish, and duration of tasks T in days since the first
    start, and their realized slack within the precedences E: how much later a task
    could have finished without delaying the last finish among T."""
    tk = net.tasks.set_index("task")
    alpha, omega = tk.alpha.dropna().to_dict(), tk.omega.dropna().to_dict()
    T = [i for i in T if i in omega]              # open tasks have no realized slack
    d = {}
    for i in T:
        if i in alpha:
            d[i] = (omega[i] - alpha[i]) / DAY
        else:
            p50 = tk.at[i, "p50"] if "p50" in tk else np.nan
            d[i] = 0.0 if pd.isna(p50) else float(p50)
    E = {p: e for p, e in E.items() if p[0] in omega and p[1] in omega}
    t0 = min(alpha.get(i, omega[i]) for i in T)
    rs = realized_slack(T, E, alpha, omega, d)
    return pd.DataFrame({"start": [(alpha[i] - t0) / DAY if i in alpha else np.nan for i in T],
                         "finish": [(omega[i] - t0) / DAY for i in T],
                         "duration": [d[i] if i in alpha else np.nan for i in T],
                         "slack": [rs[i] for i in T]}, index=T), t0


def _legend(ax, extra=()):
    handles = [Line2D([], [], color=INK, lw=0.9, label="declared"),
               Line2D([], [], color=INK, lw=0.9, ls=DASH, label="hidden (text only)"),
               plt.Rectangle((0, 0), 1, 1, fc=CRIT, ec=INK, lw=0.5, label="critical (no slack)"),
               *extra]
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.0), fontsize=7,
              frameon=False, ncol=len(handles), handlelength=2.2, columnspacing=1.5)


def _title_lines(title, width, lines=2):
    out = wrap(title, width)
    if len(out) > lines:
        out = out[:lines - 1] + [shorten(" ".join(out[lines - 1:]), width, placeholder="…")]
    return "\n".join(out)


def _ports(nodes, edges, pos, h):
    """Spread the arrows that leave (or enter) a box over its right (left) side,
    ordered by the vertical position of the other task."""
    out, inc = {}, {}
    for n in nodes:
        for side, sel, other in ((out, lambda p: p[0] == n, 1), (inc, lambda p: p[1] == n, 0)):
            ps = sorted((p for p in edges if sel(p)), key=lambda p: -pos[p[other]][1])
            for k, p in enumerate(ps):
                side[p] = 0.0 if len(ps) == 1 else h * 0.35 * (1 - 2 * k / (len(ps) - 1))
    return out, inc


def plot_pert(net, tasks=None, labels=None, titles=None, ax=None, title_chars=27):
    """PERT chart (activity on node) of the project network: tasks as boxes with
    observed duration and realized slack, precedences as arrows labelled with their
    type. Columns follow the longest path from the first tasks; separate groups of
    connected tasks are stacked."""
    lab, ttl = labels or {}, titles or {}
    T, E = subnetwork(net, tasks)
    times, _ = realized_times(net, T, E)
    T = list(times.index)
    E = {p: e for p, e in E.items() if p[0] in times.index and p[1] in times.index}
    G = nx.DiGraph()
    G.add_nodes_from(T)
    G.add_edges_from(E)
    COL, ROW, w, h = 2.6, 1.15, 1.9, 0.9
    pos, top = {}, 0.0
    for comp in sorted(nx.weakly_connected_components(G), key=lambda c: (-len(c), min(c))):
        H = G.subgraph(comp)
        gens, row = [sorted(g) for g in nx.topological_generations(H)], {}
        for k, gen in enumerate(gens):
            if k:                                  # barycenter ordering against crossings
                gen.sort(key=lambda n: np.mean([row[p] for p in H.predecessors(n)]))
            for r, n in enumerate(gen):
                row[n] = r
        height = max(len(g) for g in gens)
        for k, gen in enumerate(gens):
            for r, n in enumerate(gen):
                pos[n] = (COL * k, -ROW * (top + (height - len(gen)) / 2 + r))
        top += height + 0.25
    xmax, ymin = max(x for x, _ in pos.values()), min(y for _, y in pos.values())
    if ax is None:
        _, ax = plt.subplots(figsize=(max(5, 0.92 * (xmax + w + 0.4)), max(2.2, 0.62 * (-ymin + h + 0.9))))
    crit = {i for i in T if times.at[i, "slack"] < 1e-6}
    for i, (x, y) in pos.items():
        c = i in crit
        ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle="round,pad=0,rounding_size=0.08",
                                    fc=CRIT if c else NODE, ec=INK, lw=0.6, zorder=2))
        fg = "white" if c else INK
        d, s = times.at[i, "duration"], times.at[i, "slack"]
        ax.text(x, y + 0.29, lab.get(i, i), ha="center", va="center", fontsize=7.5, weight="bold",
                color=fg, zorder=3)
        if ttl.get(i):
            ax.text(x, y + 0.03, _title_lines(ttl[i], title_chars), ha="center", va="center", fontsize=6.2,
                    color=fg, zorder=3, linespacing=1.1)
        ax.text(x, y - 0.3, ("start unknown" if np.isnan(d) else f"{d:.1f} d") + f" · slack {s:.1f} d",
                ha="center", va="center", fontsize=6.2, color=fg, zorder=3)
    out, inc = _ports(T, E, pos, h)
    for (i, j), e in E.items():
        (xi, yi), (xj, yj) = pos[i], pos[j]
        a, b = (xi + w / 2, yi + out[(i, j)]), (xj - w / 2, yj + inc[(i, j)])
        ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=8,
                                     lw=1.6 if i in crit and j in crit else 0.8, color=INK,
                                     ls=DASH if e["hidden"] else "-", shrinkA=0, shrinkB=0, zorder=1))
        f = 1 - 0.3 / max(b[0] - a[0], 0.3)        # label just before the arrowhead
        ax.text(a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]), e["type"], fontsize=5.8, color=INK,
                ha="center", va="center", zorder=3, bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none"))
    ax.set_xlim(-w / 2 - 0.2, xmax + w / 2 + 0.2)
    ax.set_ylim(ymin - h / 2 - 0.2, h / 2 + 0.2)
    ax.axis("off")
    _legend(ax)
    return ax


def plot_gantt(net, tasks=None, labels=None, order=None, ax=None, unit="day"):
    """Gantt chart of the project network: observed start and finish of each task,
    precedences as arrows between the constrained ends (FS: finish to start, FF:
    finish to finish, SS: start to start, SF: start to finish), and realized slack."""
    lab = labels or {}
    T, E = subnetwork(net, tasks)
    times, t0 = realized_times(net, T, E)
    T = list(order) if order else sorted(times.index, key=lambda i: (np.nan_to_num(times.at[i, "start"],
                                                                                    nan=times.at[i, "finish"]),
                                                                      times.at[i, "finish"]))
    T = [i for i in T if i in times.index]
    E = {p: e for p, e in E.items() if p[0] in times.index and p[1] in times.index}
    y = {t: len(T) - 1 - n for n, t in enumerate(T)}
    if ax is None:
        _, ax = plt.subplots(figsize=(7, 0.42 * len(T) + 1.1))
    H, end = 0.42, times.finish.max()
    for t in T:
        s, f, sl = times.at[t, "start"], times.at[t, "finish"], times.at[t, "slack"]
        crit = sl < 1e-6
        if np.isnan(s):                            # unknown start: finish only
            ax.plot([f], [y[t]], marker="D", ms=4, color=CRIT if crit else MUTED, zorder=3)
        else:
            ax.barh(y[t], max(f - s, end / 400), left=s, height=H, color=CRIT if crit else BAR,
                    edgecolor=INK, linewidth=0.5, zorder=2)
        ax.text(end * 1.06, y[t], f"{sl:.1f}", va="center", ha="right", fontsize=7, color=INK)
    point = {"FS": ("finish", "start"), "FF": ("finish", "finish"), "SS": ("start", "start"),
             "SF": ("start", "finish")}
    for (i, j), e in E.items():
        a, b = point[e["type"]]
        xi, xj = times.at[i, a], times.at[j, b]
        if np.isnan(xi) or np.isnan(xj):
            continue
        yj = y[j] + (-H / 2 if y[j] > y[i] else H / 2)
        ax.add_patch(FancyArrowPatch((xi, y[i]), (xj, yj), arrowstyle="-|>", mutation_scale=7, lw=0.8,
                                     color=INK, ls=DASH if e["hidden"] else "-", zorder=3, shrinkA=0,
                                     shrinkB=0, connectionstyle=f"arc3,rad={-0.3 if e['hidden'] else 0}"))
    ax.axvline(end, color=MUTED, lw=0.6, ls=":")
    ax.text(end * 1.06, len(T) - 0.45, f"slack ({unit[0]})", fontsize=6.5, color=MUTED, ha="right",
            va="bottom")
    ax.set_yticks([y[t] for t in T], [lab.get(t, t) for t in T])
    ax.set_xlim(min(-0.02 * end, times.start.min(skipna=True) if times.start.notna().any() else 0),
                end * 1.08)
    ax.set_ylim(-0.7, len(T) - 0.1)
    ax.set_xlabel(f"{unit}s since {pd.to_datetime(t0, unit='s'):%Y-%m-%d}")
    ax.spines[["top", "right"]].set_visible(False)
    _legend(ax)
    return ax
