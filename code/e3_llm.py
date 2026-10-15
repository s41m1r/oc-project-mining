"""E3 (Step 3): textual evidence extracted by an LLM from pairs of tasks.

Candidate pairs: the text of one task mentions the other task's key, or the
epic of the other task (a group reference such as "all issues of epic X").
For each pair, the LLM receives title, epic, components, description and
comments of both tasks (prompt: prompts/e3-dependency.txt) and returns a
relation type, the predecessor, a verbatim quote, and a confidence. Answers
whose quote does not occur in the input are discarded.

Answers are cached in results/e3/<name>.jsonl (one line per pair), so runs
can be resumed and repeated without new API calls.
"""
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
PROMPT = (BASE / "prompts" / "e3-dependency.txt").read_text()
SYSTEM = PROMPT.split("System:\n", 1)[1].split("\nUser:\n", 1)[0].strip()
CACHE_DIR = BASE / "results" / "e3"
# LLM backend: E3_PROVIDER = "openai" or "anthropic"; E3_MODEL = model name;
# optional E3_EFFORT (OpenAI reasoning_effort / Anthropic effort).
PROVIDER = os.environ.get("E3_PROVIDER", "openai")
MODEL = os.environ.get("E3_MODEL", "claude-opus-5-5" if PROVIDER == "anthropic" else "")
EFFORT = os.environ.get("E3_EFFORT", "medium" if PROVIDER == "anthropic" else "")
MAX_DESC, MAX_COMMENT, MAX_COMMENTS = 4000, 1500, 30   # characters / comments per task (documented)
KEY_RE = re.compile(r"\b[A-Z][A-Z0-9_]+-\d+\b")

SCHEMA = {
    "type": "object",
    "properties": {
        "relation": {"type": "string", "enum": ["FS", "SS", "FF", "SF", "NONE"]},
        "predecessor": {"type": "string", "enum": ["i", "j"]},
        "quote": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["relation", "predecessor", "quote", "confidence"],
    "additionalProperties": False,
}


def task_texts(L, T):
    """Title, description, epic, components and comments of each task, from the OCEL."""
    from mining import rel
    epic, comp = rel(L, "part of epic"), rel(L, "component")
    comp_name = {}
    ev = L.ev
    com = ev[(ev.ocel_type == "comment") & (ev.ocel_qualifier == "issue") & ev.ocel_object_id.isin(T)]
    ctext = L.attrs["comment"]["text"] if "comment" in L.attrs else pd.Series(dtype=str)
    comments = {}
    for r in com.sort_values("t").itertuples():
        comments.setdefault(r.ocel_object_id, []).append((r.ocel_time[:10], ctext.get(r.ocel_event_id, "")))
    out = {}
    for i in T:
        row = L.issues.loc[i]
        out[i] = {"key": i, "title": row["title"] or "", "description": row["description"] or "",
                  "epic": (epic.get(i) or [""])[0] or "",
                  "components": ", ".join(sorted(comp.get(i, []))),
                  "comments": comments.get(i, [])}
    return out


PROPOSE_SYSTEM = (
    "You plan software projects. Given the issues of one project, list the precedence "
    "dependencies between them: which issue has to finish (or start) before which other "
    "issue can start (or finish). Use FS, SS, FF, or SF. Only list dependencies you can "
    "justify from the issues.")
PROPOSE_SCHEMA = {"type": "object", "properties": {"dependencies": {"type": "array", "items": {
    "type": "object", "properties": {"predecessor": {"type": "string"}, "successor": {"type": "string"},
                                     "type": {"type": "string", "enum": ["FS", "SS", "FF", "SF"]}},
    "required": ["predecessor", "successor", "type"], "additionalProperties": False}}},
    "required": ["dependencies"], "additionalProperties": False}


def propose(L, T, name, use_llm=True):
    """The LLM proposes dependencies from the backlog of the project (titles and
    descriptions of all tasks). Cached in results/e3/proposals/<name>.json.
    Used as baseline B2 and as additional E3 candidates (hybrid)."""
    path = cache_path(name, ".json", "proposals")
    if path.exists():
        return json.loads(path.read_text())
    if not use_llm:
        return None
    texts = task_texts(L, T)
    backlog = "\n\n".join(f"{i}: {t['title']}\n{t['description'][:800]}" for i, t in texts.items())
    text, usage, _ = complete(make_client(), PROPOSE_SYSTEM, f"Issues of project {name}:\n\n{backlog}",
                              PROPOSE_SCHEMA, max_tokens=16000)
    deps = json.loads(text)["dependencies"] if text else []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(deps, indent=1))
    with open(CACHE_DIR / "usage.jsonl", "a") as f:
        f.write(json.dumps({"name": f"propose:{name}", "model": MODEL, "effort": EFFORT, "pairs": 1, **usage}) + "\n")
    return deps


def proposed_pairs(deps, T):
    Tset = set(T)
    return sorted({tuple(sorted((d["predecessor"], d["successor"]))) for d in deps or []
                   if d["predecessor"] in Tset and d["successor"] in Tset and d["predecessor"] != d["successor"]})


def candidate_pairs(texts):
    """Unordered pairs whose texts mention each other or each other's epic."""
    by_epic = {}
    for i, t in texts.items():
        if t["epic"]:
            by_epic.setdefault(t["epic"], set()).add(i)
    mentions = {}
    for i, t in texts.items():
        blob = " ".join([t["title"], t["description"]] + [c for _, c in t["comments"]])
        mentions[i] = set(KEY_RE.findall(blob)) - {i}
    pairs = set()
    for i, ks in mentions.items():
        for k in ks:
            if k in texts:
                pairs.add(tuple(sorted((i, k))))
            for j in by_epic.get(k, ()):          # reference to an epic: all its tasks
                if j != i:
                    pairs.add(tuple(sorted((i, j))))
    return sorted(pairs)


def render(t):
    comments = t["comments"][-MAX_COMMENTS:]
    lines = [f"Issue {{}}: {t['key']}", f"Title: {t['title']}", f"Epic: {t['epic'] or '-'}",
             f"Components: {t['components'] or '-'}",
             f"Description: {t['description'][:MAX_DESC]}", "Comments:"]
    lines += [f"[{d}] {c[:MAX_COMMENT]}" for d, c in comments] or ["-"]
    return "\n".join(lines)


def user_message(ti, tj):
    return render(ti).replace("{}", "i", 1) + "\n\n" + render(tj).replace("{}", "j", 1) + \
        "\n\nAnswer with the JSON object only."


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


def cache_path(name, suffix=".jsonl", sub=None):
    """Cache file for a project name (no colons, so the package checks out on any OS)."""
    d = CACHE_DIR / sub if sub else CACHE_DIR
    return d / (name.replace(":", "_") + suffix)


def make_client():
    if not MODEL:
        raise SystemExit("Set E3_MODEL (and E3_PROVIDER) before running the LLM.")
    if PROVIDER == "openai":
        import openai
        return openai.OpenAI()
    import anthropic
    return anthropic.Anthropic()


def complete(client, system, user, schema, max_tokens=4000):
    """One structured-output request. Returns (text or None if refused, usage dict, model)."""
    if PROVIDER == "openai":
        kw = {"reasoning_effort": EFFORT} if EFFORT else {}
        resp = client.chat.completions.create(
            model=MODEL, max_completion_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "answer", "schema": schema, "strict": True}},
            **kw)
        msg = resp.choices[0].message
        u = resp.usage
        usage = {"in": u.prompt_tokens, "out": u.completion_tokens,
                 "cache_read": getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0}
        return (None if getattr(msg, "refusal", None) else msg.content), usage, resp.model
    resp = client.beta.messages.create(
        model=MODEL, max_tokens=max_tokens,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": user}])
    usage = {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens,
             "cache_read": resp.usage.cache_read_input_tokens or 0}
    if resp.stop_reason == "refusal":
        return None, usage, resp.model
    return next(b.text for b in resp.content if b.type == "text"), usage, resp.model


def ask(client, ti, tj):
    msg = user_message(ti, tj)
    text, usage, model = complete(client, SYSTEM, msg, SCHEMA)
    if text is None:
        return {"relation": "NONE", "predecessor": "i", "quote": "", "confidence": 0.0,
                "refusal": True}, usage, model
    ans = json.loads(text)
    ans["quote_ok"] = bool(ans["quote"]) and _norm(ans["quote"]) in _norm(msg)
    return ans, usage, model


def run(L, T, name, max_pairs=None, workers=8, extra_pairs=()):
    """Query all candidate pairs of T (cached). Returns the raw answers DataFrame."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path(name)
    done = {}
    if path.exists():
        for line in path.read_text().splitlines():
            r = json.loads(line)
            done[(r["i"], r["j"])] = r
    texts = task_texts(L, T)
    pairs = [p for p in sorted(set(candidate_pairs(texts)) | set(extra_pairs)) if p not in done]
    if max_pairs is not None:
        pairs = pairs[:max(0, max_pairs - len(done))]
    client = make_client()
    lock = threading.Lock()
    usage = {"in": 0, "out": 0, "cache_read": 0}

    def work(p):
        ans, u, model = ask(client, texts[p[0]], texts[p[1]])
        rec = {"i": p[0], "j": p[1], **ans, "model": model}
        with lock:
            for k in usage:
                usage[k] += u[k]
            with open(path, "a") as f:
                f.write(json.dumps(rec) + "\n")
        return rec

    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(work, p) for p in pairs]
        for k, fut in enumerate(as_completed(futs), 1):
            try:
                done[(lambda r: (r["i"], r["j"]))(fut.result())] = fut.result()
            except Exception as e:                       # keep going; failed pairs are retried next run
                print("error:", type(e).__name__, str(e)[:200])
            if k % 50 == 0:
                print(f"  {name}: {k}/{len(pairs)} pairs, tokens in/out/cache {usage}")
    if pairs:
        print(f"  {name}: {len(pairs)} new pairs, tokens {usage}")
        with open(CACHE_DIR / "usage.jsonl", "a") as f:
            f.write(json.dumps({"name": name, "model": MODEL, "effort": EFFORT, "pairs": len(pairs), **usage}) + "\n")
    return pd.DataFrame(done.values())


def load(name):
    path = cache_path(name)
    if not path.exists():
        return pd.DataFrame()
    return pd.DataFrame([json.loads(l) for l in path.read_text().splitlines()])


def as_evidence(answers, min_conf=0.0, only=None):
    """Valid answers -> {(pred, succ): {relation, confidence, quote}} for mining.build_network.
    only: optional set of unordered pairs to restrict to (e.g. mention-based candidates)."""
    out = {}
    if answers is None or len(answers) == 0:
        return out
    for r in answers.itertuples():
        if only is not None and tuple(sorted((r.i, r.j))) not in only:
            continue
        if r.relation == "NONE" or not r.quote_ok or r.confidence < min_conf:
            continue
        pair = (r.i, r.j) if r.predecessor == "i" else (r.j, r.i)
        out[pair] = {"relation": r.relation, "confidence": float(r.confidence), "quote": r.quote}
    return out
