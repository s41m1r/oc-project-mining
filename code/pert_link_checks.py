"""Do Temporal/Causal links behave like PERT precedence? Per repo, project and link category:
- fs_ok: predecessor resolved before successor started (finish-to-start)
- ff_ok: predecessor resolved before successor resolved (finish-to-finish)
- same_fv: both issues share a fix version (same release)
- planned: link created before the successor started (declared up front)

Works on data/jira_public.sqlite (Public Jira Dataset). Writes results/pert_link_checks.csv.
"""
import csv
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "jira_public.sqlite"
OUT = ROOT / "results" / "pert_link_checks.csv"

# (category, predecessor side) by link-type name; 'src' means the outward issue's owner comes first.
RULES = [
    (r"finish-start|end to start|gantt dependency|finish-to-start", "gantt FS", "src"),
    (r"finish-finish|end to end|finish-to-finish", "gantt FF", "src"),
    (r"start-start|start to start|start-to-start", "gantt SS", "src"),
    (r"start-finish|start to end|start-to-finish", "gantt SF", "src"),
    (r"preceded by", "follow", "src"),          # outward: "Precedes"
    (r"follows|sequence", "follow", "dst"),     # outward: "follows" / "follows up on"
    (r"block", "block", "src"),                 # outward: "blocks"
    (r"depend|requir", "depend", "dst"),        # outward: "depends on" / "requires"
]


def classify(name):
    for pat, cat, pred in RULES:
        if re.search(pat, name, re.I):
            return cat, pred
    return None, None


def ts(s):
    return s[:19].replace("T", " ") if s else None


def main():
    db = sqlite3.connect(DB)
    issues = {k: (p, ts(s), ts(r), set(filter(None, (fv or "").split(","))))
              for k, p, s, r, fv in db.execute("SELECT key, project, start, resolved, fix_versions FROM issue")}
    stats = {}
    for repo, src, dst, name, created in db.execute("SELECT repo, src, dst, name, created FROM link"):
        cat, side = classify(name)
        if not cat or src not in issues or dst not in issues:
            continue
        a, b = (src, dst) if side == "src" else (dst, src)
        (pa, sa, ra, fa), (pb, sb, rb, fb) = issues[a], issues[b]
        s = stats.setdefault((repo, pb, cat), dict(n=0, known=0, fs_ok=0, ff_known=0, ff_ok=0,
                                                    same_fv=0, dated=0, planned=0))
        s["n"] += 1
        if ra and sb:
            s["known"] += 1
            s["fs_ok"] += ra <= sb
        if ra and rb:
            s["ff_known"] += 1
            s["ff_ok"] += ra <= rb
        s["same_fv"] += bool(fa & fb)
        if created and sb:
            s["dated"] += 1
            s["planned"] += ts(created) <= sb

    rows = sorted(stats.items(), key=lambda kv: -kv[1]["n"])
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["repo", "project", "category", *rows[0][1].keys()])
        for (repo, proj, cat), s in rows:
            w.writerow([repo, proj, cat, *s.values()])

    pct = lambda a, b: f"{100 * a / b:4.0f}%" if b else "    -"
    print(f"{'repo':13s} {'project':12s} {'category':9s} {'n':>6s} {'FS ok':>6s} {'FF ok':>6s} {'sameFV':>6s} {'planned':>7s}")
    for (repo, proj, cat), s in rows:
        if s["n"] >= 30 or cat.startswith("gantt"):
            if s["n"] >= 10:
                print(f"{repo:13s} {str(proj):12s} {cat:9s} {s['n']:6d} {pct(s['fs_ok'], s['known'])} "
                      f"{pct(s['ff_ok'], s['ff_known'])} {pct(s['same_fv'], s['n'])}  {pct(s['planned'], s['dated'])}")


if __name__ == "__main__":
    main()
