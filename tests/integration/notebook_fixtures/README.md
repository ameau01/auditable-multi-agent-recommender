# Notebook fixtures

Frozen LLM-judge outputs used by `notebooks/02_Evaluation_and_Results.ipynb` so the notebook can display real Mid + Rich verdicts (with rationale text) without requiring a hiring manager to set up an API key.

## What's here

| File | What it is |
| :--- | :--- |
| `scenario_NN_judge.json` | One captured `JudgeClient.score()` result per scenario. Contains the judge's integer score (0-100) plus its one-paragraph rationale, plus the provider + model + timestamp. |
| `generate_judge_fixtures.py` | The one-shot script that produces those JSONs. Run once with `ANTHROPIC_API_KEY` set. ~$0.01 total cost. |

## How the notebook uses them

`notebooks/02_Evaluation_and_Results.ipynb` first runs the deterministic Shape + Correctness check (no API key needed). It then loads the captured judge fixture from this folder and displays the Mid + Rich verdict + rationale.

If the fixture file is missing (e.g., a fresh clone where `generate_judge_fixtures.py` hasn't been run yet), the notebook falls back to a `(skipped)` marker with a one-line instruction pointing the reader here.

## How to (re)generate

```bash
# From the project root
cd agent-orchestration
uv run python tests/integration/notebook_fixtures/generate_judge_fixtures.py
```

Needs `ANTHROPIC_API_KEY` in `.env` (or exported). Writes one `scenario_NN_judge.json` per scenario in this folder. Re-running overwrites them.

## Why captured, not live

The notebooks are designed for a hiring manager to open, click "Run All", and see results in 30 seconds. A live judge call would add three failure modes that defeat that goal: missing key, network outage, transient API error. Capturing once + committing the result removes all three.

The capture is honest about being captured — notebook 02 labels the displayed verdict as "captured from a real judge run on YYYY-MM-DD" so a reader knows they're looking at a real prior output, not synthesized text.
