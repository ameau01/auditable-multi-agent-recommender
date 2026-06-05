# Notebooks

A three-notebook tour through the project, in order. Each runs end-to-end with **no API key, no network calls, no setup beyond launching Jupyter**. Every cell reads from artifacts already committed in the repo — outputs from a real prior Opus end-to-end run, frozen and curated for reproducible inspection.

The total tour takes about 10 minutes if you skim, 25 minutes if you read carefully (per-notebook read times: 01 ≈ 5 min, 02 ≈ 8 min, 03 ≈ 12 min).

## The three notebooks

| Order | Notebook | What it shows |
| :---: | :--- | :--- |
| 1 | [`01_Architecture_Overview.ipynb`](01_Architecture_Overview.ipynb) | What the system does. Six agents in a hierarchy, walked end-to-end against scenario 08. Loads the bundled trace, displays the rendered report inline. |
| 2 | [`02_Evaluation_and_Results.ipynb`](02_Evaluation_and_Results.ipynb) | How good is it. A four-layer scoring model (Shape / Correctness / Mid / Rich), 18 hand-crafted gold answers, gold-vs-gold sanity check, published per-model-tier baselines. |
| 3 | [`03_Harness_Design_Deep_Dive.ipynb`](03_Harness_Design_Deep_Dive.ipynb) | How correctness is enforced. The four cross-cutting harnesses (Input, Reasoning, Action, Orchestration), a backward evidence-chain walk, mechanical proof of zero dangling references. |

## How to run

From the project root (the `agent-orchestration/` folder):

```bash
uv run --with jupyterlab jupyter lab
```

That uses the project's `uv` venv (so `import src.*` resolves) and pulls in JupyterLab ephemerally — **no permanent dep added to `pyproject.toml`**, no `pip install` to run beforehand. A browser tab opens; navigate to `notebooks/` and click any of the three.

If you prefer the classic UI, replace `jupyterlab` with `notebook` in the command above.

## What each notebook reads

| Notebook | Reads from |
| :--- | :--- |
| 01 | `dataset-examples/scenario_08/` (telemetry + main.tf), `sample_runs/traces/scenario_08_trace.json`, `sample_runs/reports/scenario_08_report.md` |
| 02 | `eval-set/expectations/*/raw_recommendation.json` (18 golds), `tests/integration/notebook_fixtures/scenario_08_judge.json` (captured judge verdict), `measurements/orchestrated-opus-opus-summary.txt` |
| 03 | `sample_runs/traces/scenario_08_trace.json` only |

All paths are relative to the repo root. Everything is already committed to git. No fixtures need to be generated.

## Want the live experience?

The notebooks deliberately use frozen artifacts so a reviewer never hits a missing API key or a stale audit DB. To produce one of these artifacts yourself with a real LLM cycle, see [`../docs/running.md`](../docs/running.md). `make scenario APP=app-08` runs the agents end-to-end on your machine; `docker compose up --build live-llm` does the same hermetic in Docker.

## `_archive/`

The previous three notebooks (`01_eval_walkthrough.ipynb`, `02_agent_orchestration_preview.ipynb`, `03_traceability_demo.ipynb`) are preserved in `_archive/` for historical reference. They were written across earlier project phases and contain stale CLI flags, file paths, and architectural claims that no longer match the current code. The new trio above supersedes them.
