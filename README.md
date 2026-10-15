# Object-Centric Project Mining: reproducibility package

Reproducibility package for the paper *Object-Centric Project Mining:
Recovering the Hidden PERT from Agile Issue Trackers* (AI-PA 2026, under review).

Object-centric project mining derives the project network of a release (tasks,
typed precedence FS/SS/FF/SF, duration distributions, critical path, slack) from
an object-centric event log (OCEL 2.0) of an issue tracker.

## Contents

| Path | Content | Paper |
|---|---|---|
| `docs/ocel-mapping.md` | Mapping of tracker concepts to OCEL elements (Jira, GitHub, Redmine) | Step 1 |
| `prompts/e3-dependency.txt` | Prompt for textual evidence (E3) | Step 3 |
| `code/tawos_to_ocel.py` | Step 1: Jira (TAWOS) records to OCEL 2.0 (SQLite) | Sect. 3.2 |
| `code/mining.py` | Steps 2-5: tasks and lifecycles, evidence, typed network, durations, CPM | Sect. 3.2 |
| `code/e3_llm.py` | E3: LLM-extracted, quote-checked textual evidence; LLM proposals (B2) | Sect. 3.2, 4 |
| `code/running_example.py` | Running example (Table 1) as an OCEL, mined | Sect. 4.2 |
| `code/eval_rq1.py` | RQ1: recovery of planned dependencies in DM epics | Sect. 4.3, Table 3 |
| `code/eval_rq2.py` | RQ2: findings on released versions of MDL, SERVER, MESOS | Sect. 4.4 |
| `code/profiles.py` | Evidence profiles and validation sample | Fig. 3, Sect. 4.4 |
| `code/fig_example.py` | Running example figure | Fig. 2 |
| `code/start_signals.py`, `code/start_fallback.py` | Missing and late task starts | Step 2, Sect. 4.1 |
| `code/pert_link_checks.py` | FS/FF behaviour of links in the Public Jira Dataset | Sect. 4.4 |
| `code/eval_f5.py` | Slip analysis (not robust; discussed in Sect. 5) | Sect. 5 |
| `code/load_tawos.py`, `code/build_events.sql`, `code/load_jira_public.py` | Data loading | Sect. 4.1 |
| `code/data_checks.py`, `code/multi_object_events.py`, `code/find_example.py`, `code/show_cluster.py`, `code/remote_zip.py` | Exploration and helpers | - |
| `results/` | All results reported in the paper (CSV/JSON), LLM answers in `results/e3/` | Sect. 4 |

The datasets are not included; see below for how to obtain them.

## Results in the paper

| Paper | File |
|---|---|
| Table 2 (data) | `results/rq2/releases.csv`, OCEL statistics from `code/tawos_to_ocel.py` |
| Table 3 (RQ1) | `results/rq1/summary.csv` (variants `B1`, `E3-mention`, `FULL-mention`, `B2`; hybrid variants `E3`, `FULL`), per epic: `results/rq1/per_epic.csv`, edges: `results/rq1/edges.csv` |
| Sect. 4.4 (RQ2) | `results/rq2/summary.csv`, `results/rq2/edges.csv`, `results/rq2/tasks.csv` |
| Fig. 3, hidden dependencies | `results/rq2/profiles.csv`; manual validation: `results/rq2/hidden_sample.csv` (column `valid`) |
| Running example | `results/running_example.json` |
| LLM answers and token usage | `results/e3/*.jsonl`, `results/e3/proposals/*.json`, `results/e3/usage.jsonl` |

All LLM answers are cached, so every script reproduces the reported numbers
without new API calls.

## Data

- **TAWOS** (Tawosi et al., MSR 2022): figshare article 21308124, file
  `TAWOS.sql.zip`, Apache 2.0. Place it in `data/raw/`.
- **Public Jira Dataset** (Montgomery et al., MSR 2022): Zenodo record
  15719919, CC-BY 4.0. Place the archive in `data/raw/jira-public/`.

## Setup

Python 3.10 or later.

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Reproducing

```sh
cd code
# data (about 30 minutes)
../.venv/bin/python load_tawos.py                 # data/tawos.sqlite
sqlite3 ../data/tawos.sqlite < build_events.sql
../.venv/bin/python load_jira_public.py           # data/jira_public.sqlite (Sect. 4.4, 84,000 links)

# Step 1: OCELs (seconds per tracker project)
../.venv/bin/python tawos_to_ocel.py MDL DM SERVER MESOS     # data/ocel/<KEY>.sqlite

# evaluation (uses the cached LLM answers in results/e3/)
../.venv/bin/python running_example.py            # Sect. 4.2
../.venv/bin/python fig_example.py                # Fig. 2
../.venv/bin/python eval_rq1.py                   # Sect. 4.3, Table 3
../.venv/bin/python eval_rq2.py                   # Sect. 4.4 (about 10 minutes)
../.venv/bin/python profiles.py                   # Fig. 3
../.venv/bin/python pert_link_checks.py           # Sect. 4.4, Public Jira Dataset
../.venv/bin/python start_signals.py              # Step 2, missing starts
```

### Querying the LLM again

E3 and B2 use an LLM with structured output. Answers already in `results/e3/`
are reused; only missing pairs are queried.

```sh
export E3_PROVIDER=openai                  # or: anthropic
export E3_MODEL=gpt-5.4-mini-2026-03-17    # model used in the paper
export E3_EFFORT=low
export OPENAI_API_KEY=...                  # or ANTHROPIC_API_KEY
../.venv/bin/python running_example.py --llm
../.venv/bin/python eval_rq1.py --llm
../.venv/bin/python eval_rq2.py MDL:2.5 SERVER:4.3.1 MESOS:1.0.0
```

The paper's run used 1,859 requests and cost less than US$5.

## Parameters

Defined at the top of `code/mining.py`: evidence weights (declared 1, removed
link -1, structural 0.25 up to 0.5), threshold 0.5, hand-over window 3 days,
minimum reference class size 10, 1,000 duration samples. Statuses of the
in-progress category per tracker project: `IN_PROGRESS` in `code/tawos_to_ocel.py`.

## License

Code: MIT (see `LICENSE`). The datasets keep their own licenses (TAWOS: Apache 2.0,
Public Jira Dataset: CC-BY 4.0); LLM answers in `results/e3/` quote issue texts
from these datasets.
