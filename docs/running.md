# Running the system

Two paths. Pick the one that matches what you want to see.

## Path 1 — Hiring manager / quick demo (30 seconds, no setup)

Clone the repo. Run one command. Read a real-looking recommendation report.

```bash
git clone <this repo>
cd agent-orchestration
docker compose up --build demo
```

The container builds the image (cold build: a few minutes), replays a vendored audit-cycle fixture for `app-08`, and writes three files to `./demo-output/`. The `--build` flag is defensive — it picks up any source/dep changes since the last run and is a near-instant cache hit when nothing changed.

| File | What it is |
| :--- | :--- |
| `report.md`  | Human-readable recommendation, with a MOCK MODE banner at the top |
| `trace.md`   | Human-readable audit trail (every tool call, every observation, every cited reference) |
| `trace.json` | Machine-readable audit trail (same data, structured) |

**What this proves and what it doesn't:**

- *Proves:* the report rendering pipeline, the audit-trail structure, the per-scenario cross-tier reasoning the system produces, and that every claim in the report ties back to a specific observation row in the trace.
- *Does not prove:* anything about live LLM performance. The replay reconstructs the recorded reasoning chain from a real prior cycle. No LLM was called. No API key needed. No Hugging Face download. No network.

That's the point: a reviewer can see what the system produces without configuring credentials.

## Path 2 — Developer / measurement (full setup, real LLM)

For running the agents live, scoring against gold answers, reproducing the baseline table in [`../measurements/`](../measurements/), or extending the system.

### One-time setup

```bash
# 1. Install uv (the dependency manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Sync the project
uv sync

# 3. Create .env with the LLM API key
cp .env.example .env
$EDITOR .env   # set ANTHROPIC_API_KEY (the only currently supported provider; see docs/decisions.md)
```

### Running

```bash
# Score app-08's gold answer against the deterministic gates (no API key needed for Shape + Correctness).
bash scripts/run_demo.sh

# Score with the LLM judge enabled (adds Mid + Rich layers — needs an API key in .env).
bash scripts/run_demo.sh --with-judge

# Run the full multi-agent pipeline against all 18 scenarios in the dataset
# (uses SPECIALIST_MODEL + EVALUATOR_MODEL from .env; ~30-50 min, ~$1-$5 depending on model tier).
bash scripts/integration_test_all.sh

# Single-shot baseline (one LLM call per scenario, no orchestration).
bash scripts/baseline_single_shot.sh --model haiku   # ~$0.05, ~3 min
bash scripts/baseline_single_shot.sh --model sonnet  # ~$0.50, ~5-8 min
bash scripts/baseline_single_shot.sh --model opus    # ~$3,    ~8-15 min

# Summarize any run folder into a polished per-layer table.
python3 tests/baseline_summarize.py <run-dir> --output measurements/<name>-summary.txt
```

### Inspecting a live run

After an orchestrated run finishes, inspect what happened:

```bash
# List the cycles in the audit DB.
bash scripts/show_audit_trail.sh --list

# Show the audit trail for a specific app + cycle.
bash scripts/show_audit_trail.sh app-08 <cycle_id>

# Render the report + trace for a specific cycle.
bash scripts/render_recommendation.sh app-08
bash scripts/render_evidence_trace.sh app-08 --format markdown
bash scripts/render_evidence_trace.sh app-08 --format json
```

### LangSmith tracing (optional)

If `LANGSMITH_API_KEY` is set in `.env`, every cycle's reasoning trace is exported to the LangSmith dashboard at `https://smith.langchain.com/o/<your-org>/projects/p/<your-project>`. Useful for visualizing the multi-agent graph and individual LLM calls.

### LangGraph dev / Studio (optional)

The agent graph can also be driven interactively from LangGraph Studio — useful for stepping through a cycle node-by-node, inspecting state at each transition, and replaying inputs without re-running the CLI.

```bash
make langgraph              # recommended — boots local server + opens Studio
# or: ./scripts/run_langgraph_dev.sh
```

The local server reads [`langgraph.json`](../langgraph.json) and exposes **two graphs**:

| Graph in Studio   | What it runs                                            | Needs API key? |
| :---------------- | :------------------------------------------------------ | :------------- |
| `agent`           | Live multi-agent cycle — real LLM calls end-to-end      | Yes — `ANTHROPIC_API_KEY` in `.env` |
| `agent_replay`    | Replays a vendored cycle fixture deterministically — no LLM, no network | No |

Pick `agent_replay` for a zero-cost walk-through against the bundled `app-08` fixture; pick `agent` to drive a fresh cycle.

**Inputs to provide in Studio's Input panel** (both graphs accept the same shape):

```json
{
  "application_id": "app-08",
  "cycle_id": "cycle-id-001"
}
```

- `application_id` — the scenario, always lowercase `app-NN` (e.g. `app-02`, `app-07`, `app-08`).
- `cycle_id` — any unique string for this run (e.g. `cycle-id-001`). The orchestrator stamps a generated id if you omit it on the `agent` graph; the `agent_replay` graph uses the value you provide to key its in-memory audit store.

Here's the graph and a finished cycle inside Studio (`agent` graph, `app-08`):

[![LangGraph Studio showing the agent graph on the left — supervisor routing to system_mapper, the three tier specialists, cross_tier_evaluator, gate, and cycle_complete — and on the right the structured recommendation produced by cross_tier_evaluator: finding_type=issue_found, primary_tier=database, with reasoning, root_cause, secondary_tier, specific_change, and summary fields expanded. Click for full size.](images/langgraph-screen-lores.jpg)](images/langgraph-screen-hires.jpg)


## What the dataset looks like

18 scenarios, each with telemetry (Terraform infrastructure spec + 14 days of metric history + business-context sidecar) and a hand-crafted gold recommendation. Published as `ameau01/synthesized-cloud-optimization-recommendations` on Hugging Face — pulled automatically by the system on first run.

The 3 short-circuit scenarios (apps 06, 15, 17) deliberately have "no-action" gold answers (`no_issue_found`, `diagnostic_deferral`) so the eval can test restraint, not just action recommendation.

## What's where in this repo

- `src/` — agent code (orchestrator, specialists, harnesses, evaluator, MCP server, renderer)
- `eval-set/` — gold answers + scoring rules
- `scripts/` — bash wrappers for the common workflows
- `tests/` — unit + integration tests, plus measurement scripts (`baseline_single_shot.py`, `baseline_summarize.py`, `run_replay.py`)
- `sample_runs/` — three vendored reports + traces from real prior Opus runs
- `measurements/` — per-layer baseline scores across six runs (the source of the README table)
- `docs/` — the deeper docs (eval-set design, audit-trail design, MCP server design, decisions log)
