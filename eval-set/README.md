# eval-set

The benchmark dataset. Pure data: 18 composite recommendations (each
bundling a gold answer with its scoring rubric) plus one demo script that
shows how the evaluator scores a single recommendation. No tests live
here; tests live in [`tests/`](../tests/).

For the four-layer evaluator that scores predictions against this data,
see [`docs/eval-set.md`](../docs/eval-set.md).

## What's here

```
eval-set/
├── expectations/
│   ├── 01/raw_recommendation.json   composite: gold answer + scoring rubric
│   ├── 02/raw_recommendation.json
│   ├── ...
│   └── 18/raw_recommendation.json
├── demo_scoring.py                  scores one gold (app-08) as a usage example
└── README.md                        (this file)
```

Each `expectations/NN/raw_recommendation.json` is a composite: the
top-level prediction fields are the gold answer, and the
`scoring_metadata` block holds the per-scenario rubric the evaluator
uses. The Pydantic schema lives at
[`src/models/composite.py`](../src/models/composite.py).

The 18 gold answers descend from the hand-crafted recommendations at
[`ameau01/synthesized-cloud-optimization-recommendations`](https://huggingface.co/datasets/ameau01/synthesized-cloud-optimization-recommendations).
The local copies now bundle the scoring rubric inside the same file
(the composite layout); the published Hugging Face dataset still ships
the original flat-file shape and is one revision behind on naming.

## How the pieces fit

| Folder/file                                       | Role                                                          |
|---------------------------------------------------|---------------------------------------------------------------|
| `eval-set/expectations/NN/raw_recommendation.json`| Composite: gold answer (top-level) + rubric (scoring_metadata)|
| `src/models/composite.py`                         | Pydantic schema; defines the composite shape and validators.  |
| `src/evaluator/`                                  | Pure scoring code. Reads composites from here.                |
| `src/evaluator/eval.py`                    | CLI entry point (`--app-name`, `--prediction`, `--no-judge`).       |
| `src/evaluator/evaluator.py`               | `Scorer` class for Python API use.                                  |
| `src/evaluator/judge_client.py`            | Anthropic SDK wrapper for the LLM judge (Mid + Rich).               |
| `src/evaluator/prompts/judge_richness.md`  | Global scoring prompt used by the judge.                            |
| `tests/integration/test_golden_answers.py` | Pytest: every gold passes every deterministic layer.                |
| `tests/integration/test_edge_cases.py`     | Pytest: bad mocks fail the expected layer (with judge mocking).     |
| `tests/judge_live/`                        | Opt-in tests that exercise the real Anthropic judge.                |

`src/evaluator/` is pure Python with no data files. `eval-set/` is pure
data. The dependency runs one way: `src/evaluator/` reads composites
from `eval-set/`, never the reverse. `src/models/` is the schema
definition shared by both sides.

## App-name convention

User-facing entry points (CLI, MCP server) refer to scenarios as
`app-NN` (e.g. `app-08`) rather than bare scenario ids. This matches
the project's mental model: an alert fires against a named application;
the agent pulls telemetry for that application; the evaluator scores
the resulting recommendation for that application. The mapping `app-NN
→ scenario id NN` is the only indirection.

The Python library API (`Scorer` class) operates at the
dataset-internal level and uses `scenario_id`; the translation happens
at the CLI surface.

## Two-mode evaluator

The four layers split into two modes (see [`docs/eval-set.md`](../docs/eval-set.md) for the full design):

- **Deterministic gates.** Shape (well-formed JSON) and Correctness
  (strict enum equality on the four decision fields). Pure-Python,
  reproducible, no API key required.
- **LLM judge.** Mid and Rich score the `specific_change` prose against
  the gold via a pinned LLM call (temperature 0). Mid passes if
  score >= 30; Rich passes if score >= 60 AND the four deterministic
  structural checks pass (fixture_citation, cost_impact_quantified,
  projected_state_quantified, evidence_structured).

The judge supports either OpenAI or Anthropic:

- Set `OPENAI_API_KEY` in `.env` for OpenAI (default model `gpt-4o-mini`).
- Set `ANTHROPIC_API_KEY` in `.env` for Anthropic (default model
  `claude-haiku-4-5-20251001`).
- `LLM_JUDGE_PROVIDER` (`openai` or `anthropic`) picks the provider
  explicitly when both keys are set; otherwise the provider is
  auto-detected and prefers OpenAI.
- `LLM_JUDGE_MODEL` overrides the default model for the chosen provider.

When neither key is set, Mid and Rich return `(skipped)` markers and
the report-format contract holds. The CLI and the demo both honor this
graceful-degradation path.

**Calibration caveat.** The default thresholds (Mid >= 30, Rich >= 60)
were originally calibrated against Anthropic Haiku and verified against
OpenAI gpt-4o-mini. Switching providers or models may shift borderline
verdicts; re-run `tests/judge_live/` after any change to confirm
gold-vs-gold self-validation still clears the high-richness band.

## Four ways to use this folder

> **Prerequisite for all CLI / Python-API uses.** Run `uv sync` once
> from the project root so that the `src` package is installed in the
> venv. After that, the commands below work from any directory.

### 1. As reference

Open any `expectations/NN/raw_recommendation.json` to see what a
complete, well-formed composite looks like for that scenario: the
top-level prediction fields are the gold recommendation, the
`scoring_metadata` block is the rubric.

### 2. Score one prediction (CLI)

With the LLM judge (default; needs `ANTHROPIC_API_KEY` in `.env`):

```bash
uv run python -m src.evaluator.eval \
    --app-name app-08 \
    --prediction my_prediction.json
```

Deterministic-only (no API key needed):

```bash
uv run python -m src.evaluator.eval \
    --app-name app-08 \
    --prediction my_prediction.json \
    --no-judge
```

Output: per-layer verdict (Shape / Correctness / Mid / Rich) + a
one-line summary.

Exit codes:
- `0` if every layer that ran passed (graceful skips don't count as failures)
- `1` if any layer that ran actually failed
- `2` on usage error (missing file, malformed JSON, unknown app)

### 3. Score programmatically (Python API)

```python
import json
from pathlib import Path
from src.evaluator import Scorer
from src.evaluator.judge_client import JudgeClient

# Build the judge if an API key is available, otherwise None (graceful skip).
judge = JudgeClient() if JudgeClient.is_available() else None

# Build once, score many
s = Scorer.from_eval_set_dir(
    "eval-set/",
    dataset_examples_dir="dataset-examples/",
    judge=judge,
)

prediction = json.loads(Path("my_prediction.json").read_text())
result = s.score_one("08", prediction)

print(result["shape"].passed)        # bool
print(result["correctness"].passed)  # bool
# Mid + Rich are TierResult when scored, the string "skipped" only when
# Correctness fails (the structural gate). When the judge is unavailable,
# Mid + Rich are TierResult with a 'skipped' check inside.
```

The Scorer caches rules + gold answers at init; subsequent
`score_one` calls reuse them. The name `Scorer` (not `Evaluator`) keeps
this class distinct from the Cross-Tier Evaluator agent that *produces*
the recommendation being scored.

### 4. Run the demo (see the evaluator on one scenario)

Deterministic only (default; no API key):

```bash
uv run python eval-set/demo_scoring.py
```

With the LLM judge:

```bash
uv run python eval-set/demo_scoring.py --with-judge
```

Output: scores the gold for `app-08` and prints the four-layer table.
This is a usage example for a human reviewer.

To verify all 18 gold answers still pass their own evaluator, run the
integration test instead (next section).

## How to verify

Three levels of verification, depending on what you want to check.

### Verify one scenario via the CLI

```bash
# Score the gold for app-08 against its own rules (deterministic only).
uv run python -m src.evaluator.eval \
    --app-name app-08 \
    --prediction eval-set/expectations/08/raw_recommendation.json \
    --no-judge
```

Expected: exit code 0, "Shape and Correctness passed..." on stdout.

With the LLM judge enabled (drop `--no-judge`; requires API key):

Expected: exit code 0, "All layers passed." on stdout.

Pick a different `app-NN` (between `app-01` and `app-18`) and the
matching `expectations/NN/raw_recommendation.json` to verify a different
scenario.

### Verify all 18 scenarios via pytest (deterministic)

```bash
# Headline benchmark-integrity test: every gold passes every layer.
uv run pytest tests/integration/test_golden_answers.py -v

# Or via the wrapper script:
scripts/run_golden.sh -v
```

Expected: 94 tests pass (covering Shape, Correctness, Mid, Rich, and
short-circuit semantics across all 18 scenarios with both metadata-loaded
and metadata-omitted paths).

If anything fails here, the gold answers and the scoring rules have
drifted apart. Fix one or the other before publishing.

### Verify the full suite (data + behavior + CLI)

```bash
# Full default suite: data validation + scorer behavior + edge cases + CLI.
uv run pytest -q

# Or:
scripts/run_integration.sh -v
```

Expected: 415 passed, 3 skipped. The 3 skips are CLI discrimination
tests that need `ANTHROPIC_API_KEY` to assert end-to-end failure-mode
discrimination through the live judge. They run automatically when the
key is set.

### Verify the LLM judge against itself (opt-in, costs API calls)

```bash
uv run pytest tests/judge_live/ -v
```

15 API calls to Anthropic (~$0.02 total at Haiku pricing, ~45 seconds).
Each asserts that a gold's `specific_change` scores >= 75 when judged
against itself, plus one tighter test on scenario 08. If any scenario
undershoots, the prompt or the gold's prose needs tightening.

This suite is excluded from the default `pytest` collection (configured
in `pyproject.toml`); run it explicitly when iterating on the judge
prompt or after dataset edits.

## Coverage at a glance

| Finding type           | Scenarios                                              | Count |
|------------------------|--------------------------------------------------------|-------|
| `issue_found`          | 01, 02, 03, 04, 05, 07, 08, 09, 10, 11, 12, 13, 14, 16, 18 | 15    |
| `no_issue_found`       | 06                                                     | 1     |
| `diagnostic_deferral`  | 15, 17                                                 | 2     |

| Primary tier   | Scenarios                              | Count |
|----------------|----------------------------------------|-------|
| `compute`      | 01, 02, 09, 11, 13, 14, 16, 18         | 8     |
| `database`     | 03, 04, 08, 12                         | 4     |
| `cache`        | 07                                     | 1     |
| `network`      | 05, 10                                 | 2     |
| `deferred`     | 15, 17                                 | 2     |
| `null`         | 06                                     | 1     |

## Provenance

The 18 composites diverge from the published Hugging Face dataset in
three ways:

  - **Strict enum equality.** Correctness uses single-value allow-lists;
    new sentinels `deferred` (for diagnostic-deferral scenarios) and
    `cache_capacity_adjustment` (for cache-pressure scenarios) appear
    locally but not yet on Hugging Face. A short-circuit rule bypasses
    Mid + Rich for no-action findings.
  - **Threshold-gated Mid + Rich.** Mid and Rich are scored by a pinned
    LLM judge (OpenAI or Anthropic) with a 30 / 60 score gate; the
    earlier deterministic-placeholder fields (`action_keyword_groups`,
    `action_keyword_min_match`, `multi_tier_evidence`) have been
    removed from the rubric.
  - **Composite layout.** The legacy `expectations/NN.json` (gold) and
    `scoring_rules/NN/rules.json` (rubric) are merged into one
    `expectations/NN/raw_recommendation.json` per scenario, validated by
the `Composite` Pydantic schema. The local copies are the source of
truth; the published HF dataset is one revision behind. Re-publication
is a separate task.

## License

MIT. See the project root `LICENSE`.
