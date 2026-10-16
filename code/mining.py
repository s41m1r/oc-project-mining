"""Steps 2-5 of object-centric project mining. Operates on an OCEL 2.0 SQLite
file only (see tawos_to_ocel.py for Step 1).

    L = load_ocel("data/ocel/MDL.sqlite")
    net = mine(L, "release:1234", e3=answers)      # or an epic key, e.g. "DM-123"

Returns a ProjectNetwork with tasks (alpha, omega, durations, CPM results)
and edges (precedence with type, support, evidence, hidden flag).
"""
import math
import sqlite3
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pandas as pd

DAY = 86400.0

# ----------------------------------------------------------------------------
# Link semantics (A3): description of a link type -> who precedes, stated type.
# "x" = the source issue of the description ("This issue <desc> y").
# kind: "declared" = dependency without a stated type, "typed" = planned
# precedence with a stated type (Gantt and FS/FF/SS dependency link types).
# ----------------------------------------------------------------------------
LINK_SEMANTICS = {
    "blocks": ("x", None, "declared"), "is blocked by": ("y", None, "declared"),
    "depends on": ("y", None, "declared"), "is depended on by": ("x", None, "declared"),
    "depends upon": ("y", None, "declared"), "is depended upon by": ("x", None, "declared"),
    "requires": ("y", None, "declared"), "is required by": ("x", None, "declared"),
    "will help resolve": ("x", None, "declared"), "will be (partly) resolved by": ("y", None, "declared"),
    "causes": ("x", None, "declared"), "is caused by": ("y", None, "declared"),
    "breaks": ("x", None, "declared"), "is broken by": ("y", None, "declared"),
    "caused a regression": ("x", None, "declared"), "is a regression caused by": ("y", None, "declared"),
    "has to be done before": ("x", "FS", "typed"), "has to be done after": ("y", "FS", "typed"),
    "fs-depends on": ("y", "FS", "typed"), "is fs-depended by": ("x", "FS", "typed"),
    "ff-depends on": ("y", "FF", "typed"), "is ff-depended by": ("x", "FF", "typed"),
    "ss-depends on": ("y", "SS", "typed"), "is ss-depended by": ("x", "SS", "typed"),
    "is triggering": ("x", "SF", "typed"), "is triggered by": ("y", "SF", "typed"),
    "has to be finished together with": ("x", "FF", "typed"),
    "has to be started together with": ("x", "SS", "typed"),
}
DONE_RESOLUTIONS = {"Fixed", "Done", "Implemented", "Resolved", "Complete", "Completed"}

# Evidence weights and threshold (Step 4); reported in Sect. 4.1.
W_DECLARED, W_REMOVED, W_STRUCT, MAX_STRUCT, THETA = 1.0, -1.0, 0.25, 2, 0.5
HANDOVER_DAYS = 3.0
K_MIN = 10            # minimum reference class size (Step 5)
N_SAMPLES = 1000      # duration samples for criticality


# ----------------------------------------------------------------------------
# Loading the OCEL
# ----------------------------------------------------------------------------
@dataclass
class OCEL:
    issues: pd.DataFrame           # object_Issue
    o2o: pd.DataFrame              # object_object
    ev: pd.DataFrame               # events with type, time, objects (long format)
    attrs: dict                    # event type -> attribute table
    releases: pd.DataFrame
    sprint_start: dict


def epoch_seconds(s):
    """Timestamps as seconds since the epoch, independent of the datetime resolution
    (pandas 2 parses to ns, pandas 3 may infer us or s)."""
    return pd.to_datetime(s).dt.as_unit("ns").astype("int64") / 1e9


def load_ocel(path) -> OCEL:
    con = sqlite3.connect(path)
    rd = lambda s: pd.read_sql_query(s, con)
    issues = rd("select * from object_Issue").set_index("ocel_id")
    o2o = rd("select * from object_object")
    emap = dict(rd("select ocel_type, ocel_type_map from event_map_type").values)
    times, attrs = [], {}
    for t, m in emap.items():
        df = rd(f"select * from event_{m}")
        df["ocel_type"] = t
        attrs[t] = df.set_index("ocel_id")
        times.append(df[["ocel_id", "ocel_type", "ocel_time"]])
    times = pd.concat(times)
    eo = rd("select * from event_object")
    ev = eo.merge(times, left_on="ocel_event_id", right_on="ocel_id").drop(columns="ocel_id")
    ev["t"] = epoch_seconds(ev["ocel_time"])
    releases = rd("select * from object_Release").set_index("ocel_id")
    spr = rd("select ocel_id, start_date from object_Sprint").dropna()
    sprint_start = dict(zip(spr.ocel_id, epoch_seconds(spr.start_date)))
    con.close()
    return OCEL(issues, o2o, ev, attrs, releases, sprint_start)


def rel(L, qualifier):
    """Final object-to-object relation as source -> list of targets."""
    r = L.o2o[L.o2o.ocel_qualifier == qualifier]
    return r.groupby("ocel_source_id")["ocel_target_id"].apply(list).to_dict()


# ----------------------------------------------------------------------------
# Step 2: tasks and lifecycles
# ----------------------------------------------------------------------------
def lifecycles(L):
    """alpha (first in-progress status of the issue or its sub-tasks) and
    omega (last resolution) for all issues of the OCEL, in seconds."""
    st = L.ev[(L.ev.ocel_type == "set status") & (L.ev.ocel_qualifier == "issue")]
    cat = L.attrs["set status"]["category"]
    st = st[st.ocel_event_id.map(cat) == "in progress"]
    alpha = st.groupby("ocel_object_id")["t"].min()
    res = L.ev[(L.ev.ocel_type == "resolve") & (L.ev.ocel_qualifier == "issue")]
    omega = res.groupby("ocel_object_id")["t"].max()
    done = L.issues["resolution"].isin(DONE_RESOLUTIONS)
    omega = omega[omega.index.isin(L.issues.index[done])]
    parent = {s: p[0] for s, p in rel(L, "sub-task of").items()}
    alpha = alpha.to_dict()
    for s, p in parent.items():                       # sub-tasks start their parent
        if s in alpha and (p not in alpha or alpha[s] < alpha[p]):
            alpha[p] = alpha[s]
    return alpha, omega.to_dict(), parent


def tasks_of(L, p, parent):
    """Tasks of project object p: issues with fix version p (release) or of epic p.
    Sub-tasks are aggregated into their parent; abandoned issues are excluded."""
    q = "fix version" if str(p).startswith("release:") else "part of epic"
    members = set(L.o2o[(L.o2o.ocel_qualifier == q) & (L.o2o.ocel_target_id == p)].ocel_source_id)
    res = L.issues["resolution"].reindex(list(members))
    members = {i for i in members if pd.isna(res[i]) or res[i] in DONE_RESOLUTIONS}
    typ = L.issues["issue_type"].reindex(list(members))
    members = {i for i in members if typ[i] != "Epic"}
    S = {(s, parent[s]) for s in members if s in parent and parent[s] in members}
    return sorted(members - {s for s, _ in S}), S


# ----------------------------------------------------------------------------
# Step 3: evidence
# ----------------------------------------------------------------------------
def link_evidence(L, T, include_typed=True):
    """E1: dependency links between tasks of T with their history.
    Returns dict (pred, succ) -> {stated, kind, added, removed, present}."""
    Tset = set(T)
    lk = L.ev[L.ev.ocel_type.isin(["add link", "remove link"])]
    src = lk[lk.ocel_qualifier == "source"].set_index("ocel_event_id")["ocel_object_id"]
    tgt = lk[lk.ocel_qualifier == "target"].set_index("ocel_event_id")["ocel_object_id"]
    meta = lk.drop_duplicates("ocel_event_id").set_index("ocel_event_id")[["ocel_type", "t"]]
    desc = pd.concat([L.attrs[t]["description"] for t in ("add link", "remove link") if t in L.attrs])
    out = {}
    for e in meta.sort_values("t").index:
        x, y = src.get(e), tgt.get(e)
        if x not in Tset or y not in Tset or x == y:
            continue
        sem = LINK_SEMANTICS.get(desc.get(e))
        if sem is None:
            continue
        who, stated, kind = sem
        if kind == "typed" and not include_typed:
            continue
        pair = (x, y) if who == "x" else (y, x)
        d = out.setdefault(pair, {"stated": stated, "kind": kind, "added": None, "removed": None,
                                  "present": False, "n_removed": 0})
        if meta.at[e, "ocel_type"] == "add link":
            d["added"] = d["added"] or meta.at[e, "t"]
            d["present"] = True
        else:
            d["removed"] = meta.at[e, "t"]
            d["present"] = False
            d["n_removed"] += 1
        if stated:
            d["stated"], d["kind"] = stated, kind
    return out


def structural_context(L, T):
    """E2 inputs: epic, components, assignee, sprint of each task."""
    epic, comp = rel(L, "part of epic"), rel(L, "component")
    asg, spr = rel(L, "assignee"), rel(L, "sprint")
    return {i: {"epic": (epic.get(i) or [None])[0], "components": set(comp.get(i, [])),
                "assignee": (asg.get(i) or [None])[0], "sprint": (spr.get(i) or [None])[0]}
            for i in T}


def structural_support(ctx, i, j, alpha, omega, sprint_start):
    """E2 relations supporting i -> j (count, names)."""
    a, b = ctx[i], ctx[j]
    names = []
    if a["epic"] and a["epic"] == b["epic"]:
        names.append("same epic")
    if a["components"] & b["components"]:
        names.append("shared component")
    if a["assignee"] and a["assignee"] == b["assignee"] and i in omega and j in alpha \
            and 0 <= alpha[j] - omega[i] <= HANDOVER_DAYS * DAY:
        names.append("hand-over")
    si, sj = sprint_start.get(a["sprint"]), sprint_start.get(b["sprint"])
    if si is not None and sj is not None and si < sj:
        names.append("sprint order")
    return names


# ----------------------------------------------------------------------------
# Step 4: typed precedence network
# ----------------------------------------------------------------------------
INF = float("inf")


def satisfied(tau, i, j, alpha, omega):
    """Does the observed execution satisfy tau(i, j) with lag 0? Missing finish = not
    yet finished (inf); a missing start makes start-based constraints undecidable (None)."""
    ai, aj = alpha.get(i), alpha.get(j)
    wi, wj = omega.get(i, INF), omega.get(j, INF)
    if tau == "FS":
        return None if aj is None else wi <= aj
    if tau == "SS":
        return None if ai is None or aj is None else ai <= aj
    if tau == "FF":
        return wi <= wj
    if tau == "SF":
        return None if ai is None else ai <= wj
    raise ValueError(tau)


def assign_type(stated, i, j, alpha, omega):
    """Stated type if the observed times satisfy it; a stated type that needs an
    unknown start or that the observed times violate is weakened to FF. Without
    a stated type: FS if satisfied, else FF."""
    if stated:
        return stated if satisfied(stated, i, j, alpha, omega) else "FF"
    return "FS" if satisfied("FS", i, j, alpha, omega) else "FF"


def endpoint_graph(T, edges):
    """Implication graph over task endpoints: (i,'s') -> (i,'f') and one arc per precedence."""
    G = nx.DiGraph()
    for i in T:
        G.add_edge((i, "s"), (i, "f"))
    end = {"FS": ("f", "s"), "SS": ("s", "s"), "FF": ("f", "f"), "SF": ("s", "f")}
    for (i, j), e in edges.items():
        a, b = end[e["type"]]
        G.add_edge((i, a), (j, b), pair=(i, j))
    return G, end


def build_network(T, e1, e3, ctx, alpha, omega, sprint_start):
    """Combine E1-E3 into the typed precedence relation (Step 4)."""
    cand = {}
    for pair, d in e1.items():
        c = cand.setdefault(pair, {"e1": None, "e3": None})
        c["e1"] = d
    for pair, a in (e3 or {}).items():
        if pair[0] in ctx and pair[1] in ctx:
            cand.setdefault(pair, {"e1": None, "e3": None})["e3"] = a
    edges, dropped = {}, []
    for (i, j), c in cand.items():
        s2 = structural_support(ctx, i, j, alpha, omega, sprint_start)
        sup = 0.0
        if c["e1"]:
            sup += W_DECLARED if c["e1"]["present"] else W_REMOVED
        if c["e3"]:
            sup += c["e3"]["confidence"]
        sup += W_STRUCT * min(len(s2), MAX_STRUCT)
        stated = (c["e1"] or {}).get("stated") or (c["e3"] or {}).get("relation")
        tau = assign_type(stated, i, j, alpha, omega)
        rec = {"type": tau, "stated": stated, "support": sup, "e2": s2,
               "declared": bool(c["e1"] and c["e1"]["present"]),
               "removed": bool(c["e1"] and c["e1"]["n_removed"]),
               "kind": (c["e1"] or {}).get("kind"),
               "added": (c["e1"] or {}).get("added"),
               "textual": bool(c["e3"]), "quote": (c["e3"] or {}).get("quote")}
        rec["hidden"] = rec["textual"] and not rec["declared"]
        if sup <= THETA:
            dropped.append(((i, j), "support", rec))
        elif satisfied(tau, i, j, alpha, omega) is False:
            dropped.append(((i, j), "violated", rec))
        else:
            edges[(i, j)] = rec
    # cycles: remove the precedence with the lowest support in each cycle
    G = nx.DiGraph(list(edges))
    while True:
        try:
            cyc = nx.find_cycle(G)
        except nx.NetworkXNoCycle:
            break
        weakest = min(((u, v) for u, v, *_ in cyc), key=lambda p: edges[p]["support"])
        dropped.append((weakest, "cycle", edges.pop(weakest)))
        G.remove_edge(*weakest)
    # transitive reduction over endpoints: drop i->k if another path implies it
    for pair in sorted(edges, key=lambda p: edges[p]["support"]):
        EG, end = endpoint_graph(T, edges)
        a, b = end[edges[pair]["type"]]
        EG.remove_edge((pair[0], a), (pair[1], b))
        if nx.has_path(EG, (pair[0], a), (pair[1], b)):
            dropped.append((pair, "transitive", edges.pop(pair)))
    return edges, dropped


# ----------------------------------------------------------------------------
# Step 5: durations and critical path
# ----------------------------------------------------------------------------
def kaplan_meier(d, observed):
    """Kaplan-Meier survival curve; returns (times, survival)."""
    order = np.argsort(d)
    d, observed = np.asarray(d)[order], np.asarray(observed)[order]
    times, surv, s, n = [], [], 1.0, len(d)
    for t in np.unique(d):
        at_risk = (d >= t).sum()
        events = ((d == t) & observed).sum()
        if events:
            s *= 1 - events / at_risk
            times.append(t)
            surv.append(s)
    return np.array(times), np.array(surv)


def km_quantile(times, surv, q, fallback):
    """Smallest t with S(t) <= 1 - q."""
    idx = np.nonzero(surv <= 1 - q)[0]
    return float(times[idx[0]]) if len(idx) else fallback


def reference_classes(L, alpha, omega, cutoff):
    """Durations (days) of tasks of the OCEL started before the cutoff; tasks not
    finished by the cutoff are censored there (no information after the cutoff)."""
    comp = rel(L, "component")
    rows = []
    for i, a in alpha.items():
        if a >= cutoff or i not in L.issues.index:
            continue
        w = omega.get(i)
        obs = w is not None and w <= cutoff
        d = ((w if obs else cutoff) - a) / DAY
        rows.append((i, max(d, 0.0), obs, L.issues.at[i, "issue_type"],
                     (sorted(comp.get(i, [])) or [None])[0], L.issues.at[i, "story_points"]))
    return pd.DataFrame(rows, columns=["task", "d", "obs", "issue_type", "component", "sp"])


def duration_model(ref, issue_type, component, sp):
    """Back off from (issue type, component, story points) to coarser classes."""
    levels = [((ref.issue_type == issue_type) & (ref.component == component) & (ref.sp == sp)),
              ((ref.issue_type == issue_type) & (ref.component == component)),
              (ref.issue_type == issue_type),
              pd.Series(True, index=ref.index)]
    for lvl, m in enumerate(levels):
        if (pd.isna(sp) and lvl == 0) or (component is None and lvl in (0, 1)):
            continue
        cls = ref[m]
        if cls.obs.sum() >= K_MIN or lvl == 3:
            times, surv = kaplan_meier(cls.d.values, cls.obs.values)
            fb = float(cls.d.max()) if len(cls) else 1.0
            return {"level": lvl, "n": len(cls), "times": times, "surv": surv,
                    "p10": km_quantile(times, surv, .1, fb), "p50": km_quantile(times, surv, .5, fb),
                    "p90": km_quantile(times, surv, .9, fb), "max": fb}


def sample(model, rng, n):
    u = rng.random(n)
    t, s = model["times"], model["surv"]
    if len(t) == 0:
        return np.full(n, model["max"])
    idx = np.searchsorted(-s, -(1 - u), side="left")   # first index with S(t) <= 1 - u
    out = np.where(idx < len(t), t[np.minimum(idx, len(t) - 1)], model["max"])
    return out


OFFSET = {"FS": lambda di, dj: di, "SS": lambda di, dj: 0.0,
          "FF": lambda di, dj: di - dj, "SF": lambda di, dj: -dj}


def cpm_structure(T, edges):
    """Topological order and typed arcs, computed once per network."""
    G = nx.DiGraph()
    G.add_nodes_from(T)
    G.add_edges_from(edges)
    order = list(nx.topological_sort(G))
    preds = {j: [(i, OFFSET[edges[(i, j)]["type"]]) for i in G.predecessors(j)] for j in T}
    succs = {i: [(j, OFFSET[edges[(i, j)]["type"]]) for j in G.successors(i)] for i in T}
    return order, preds, succs


def cpm(T, edges, d, structure=None):
    """Earliest/latest starts and total slack with typed precedence (lag 0)."""
    order, preds, succs = structure or cpm_structure(T, edges)
    es = {i: 0.0 for i in T}
    for j in order:
        for i, off in preds[j]:
            es[j] = max(es[j], es[i] + off(d[i], d[j]))
    end = max(es[i] + d[i] for i in T) if T else 0.0
    ls = {i: end - d[i] for i in T}
    for i in reversed(order):
        for j, off in succs[i]:
            ls[i] = min(ls[i], ls[j] - off(d[i], d[j]))
    slack = {i: ls[i] - es[i] for i in T}
    return es, ls, slack, end


def realized_slack(T, edges, alpha, omega, d):
    """How much later each finished task could have finished, given the observed
    durations and the precedence network, without delaying the release (whose end
    is the last observed finish). Latest finishes LF follow from a backward pass."""
    fin = [omega[i] for i in T if i in omega]
    if not fin:
        return {}
    end = max(fin)
    G = nx.DiGraph()
    G.add_nodes_from(T)
    G.add_edges_from(edges)
    lf = {i: end for i in T}
    for i in reversed(list(nx.topological_sort(G))):
        for j in G.successors(i):
            tau, ls_j = edges[(i, j)]["type"], lf[j] - d[j] * DAY
            bound = {"FS": ls_j, "FF": lf[j], "SS": ls_j + d[i] * DAY, "SF": lf[j] + d[i] * DAY}[tau]
            lf[i] = min(lf[i], bound)
    return {i: (lf[i] - omega[i]) / DAY for i in T if i in omega}


@dataclass
class ProjectNetwork:
    p: str
    tasks: pd.DataFrame
    edges: dict
    dropped: list
    S: set = field(default_factory=set)


def mine(L, p, e3=None, include_typed=True, lc=None, rng_seed=0, durations=True, e1_off=False):
    alpha, omega, parent = lc or lifecycles(L)
    T, S = tasks_of(L, p, parent)
    e1 = {} if e1_off else link_evidence(L, T, include_typed=include_typed)
    ctx = structural_context(L, T)
    edges, dropped = build_network(T, e1, e3, ctx, alpha, omega, L.sprint_start)
    tasks = pd.DataFrame({"task": T})
    tasks["alpha"] = tasks.task.map(alpha)
    tasks["omega"] = tasks.task.map(omega)
    tasks["d_obs"] = (tasks.omega - tasks.alpha) / DAY
    if durations and T:
        starts = [alpha[i] for i in T if i in alpha]
        cutoff = min(starts) if starts else min(omega.get(i, INF) for i in T)
        ref = reference_classes(L, alpha, omega, cutoff)
        comp = rel(L, "component")
        models = {}
        for i in T:
            models[i] = duration_model(ref, L.issues.at[i, "issue_type"],
                                       (sorted(comp.get(i, [])) or [None])[0],
                                       L.issues.at[i, "story_points"]) if len(ref) else None
        for qn in ("p10", "p50", "p90"):
            tasks[qn] = tasks.task.map(lambda i: models[i][qn] if models[i] else np.nan)
        tasks["class_level"] = tasks.task.map(lambda i: models[i]["level"] if models[i] else np.nan)
        d50 = dict(zip(tasks.task, tasks.p50.fillna(1.0)))
        es, ls, slack, end = cpm(T, edges, d50)
        tasks["es"], tasks["ls"], tasks["slack"] = (tasks.task.map(es), tasks.task.map(ls),
                                                    tasks.task.map(slack))
        rng = np.random.default_rng(rng_seed)
        draws = {i: sample(models[i], rng, N_SAMPLES) if models[i] else np.ones(N_SAMPLES) for i in T}
        crit = dict.fromkeys(T, 0)
        structure = cpm_structure(T, edges)
        for k in range(N_SAMPLES):
            _, _, sl, _ = cpm(T, edges, {i: draws[i][k] for i in T}, structure)
            for i, v in sl.items():
                crit[i] += v < 1e-9
        tasks["criticality"] = tasks.task.map(lambda i: crit[i] / N_SAMPLES)
        dobs = {i: (omega[i] - alpha[i]) / DAY if i in alpha and i in omega else d50[i] for i in T}
        tasks["realized_slack"] = tasks.task.map(realized_slack(T, edges, alpha, omega, dobs))
    return ProjectNetwork(p, tasks, edges, dropped, S)
