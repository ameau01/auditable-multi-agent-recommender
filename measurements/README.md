# Measurements

Real per-layer scores from baseline runs against the 18-scenario eval-set. These files are the source of truth for the table in [`../README.md`](../README.md).

## Files

| File | Mode | Models | Source run |
| :--- | :--- | :--- | :--- |
| `single-shot-haiku-summary.txt`  | Single-shot, no agents | Haiku                              | `baseline-runs/haiku-single-shot/` |
| `single-shot-sonnet-summary.txt` | Single-shot, no agents | Sonnet                             | `baseline-runs/sonnet-single-shot/` |
| `single-shot-opus-summary.txt`   | Single-shot, no agents | Opus                               | `baseline-runs/opus-single-shot/` |
| `agentic-sonnet-haiku-summary.txt`  | Orchestrated multi-agent | Haiku specialists + Sonnet evaluator | `baseline-runs/integration-test-sonnet-haiku/` |
| `agentic-sonnet-sonnet-summary.txt` | Orchestrated multi-agent | Sonnet specialists + Sonnet evaluator | `baseline-runs/integration-test-sonnet-sonnet/` |
| `agentic-opus-opus-summary.txt`     | Orchestrated multi-agent | Opus specialists + Opus evaluator    | `baseline-runs/integration-test-opus-4-6/` |

Each file is a polished single-page summary: configuration block, generation totals, per-layer `pass / fail / n/a / total` table, and per-app verdicts.

## How a number gets here

1. **Single-shot rows** come from [`../tests/baseline_single_shot.py`](../tests/baseline_single_shot.py) — one LLM call per app, given the same MCP telemetry tools the orchestrated system uses, no specialists, no harnesses. Wrapped by [`../scripts/baseline_single_shot.sh`](../scripts/baseline_single_shot.sh) which also runs scoring and summary.
2. **Agentic rows** come from [`../scripts/integration_test_all.sh`](../scripts/integration_test_all.sh) — runs the full multi-agent pipeline against all 18 apps, scores against gold, then verifies the evidence chain end-to-end.
3. Both paths produce per-app `score.txt` files that [`../tests/baseline_summarize.py`](../tests/baseline_summarize.py) turns into the polished single-file summary you see in this folder.

## How to read the table

Each summary file shows four columns per layer:

| Column | Meaning |
| :--- | :--- |
| `pass` | The system produced the right answer for that layer |
| `fail` | The system produced an answer, the evaluator looked at it, the evaluator said it's wrong |
| `n/a`  | The layer didn't run for this app (an earlier gate failed or the run crashed before reaching this layer) |
| `total` | Always 18 (the eval-set size) |

`pass` includes a special case: a "correct short-circuit." Three scenarios in the eval-set (apps 06, 15, 17) have a gold answer of "no action needed" or "wait for more data." For those, the LLM judge correctly *skips* the Mid and Rich richness checks — there's no rich-text recommendation to judge when the correct verdict is "do nothing." The system gets credit for those skips because they match the gold answer.

## False positives and false negatives

The eval-set leans toward false negatives (counting a correct answer as wrong) rather than false positives (counting a wrong answer as right). The deterministic gates (Shape and Correctness) reject anything that doesn't match the gold exactly on JSON shape and on the enum fields (`finding_type`, `primary_tier`, `action_category`). A recommendation that gets the right *idea* but picks a different enum value — e.g., `cache_capacity_adjustment` when gold says `query_cache_optimization` — gets a Correctness FAIL even though a human reviewer would call it close.

This strictness is deliberate: a benchmark that grades a model leniently produces numbers that drift up as models get smarter at sounding right, which is the opposite of what a decision-support system needs. If a recommendation can't agree with gold on the basic enum vocabulary, downstream Mid and Rich richness checks are gated out (the layer reads `n/a`), and the result counts as not-pass.

The judge-scored layers (Mid and Rich) can in principle false-positive — the LLM judge could read a vague recommendation as rich and pass it. The published rubric in [`../src/evaluator/prompts/judge_richness.md`](../src/evaluator/prompts/judge_richness.md) is meant to make those judgments inspectable; per-app judge rationales are in each run's `score.txt`.

## Reproducing

```bash
# Single-shot (each model ~$0.05 haiku / ~$0.50 sonnet / ~$3 opus, ~3-10 min)
bash scripts/baseline_single_shot.sh --model haiku
bash scripts/baseline_single_shot.sh --model sonnet
bash scripts/baseline_single_shot.sh --model opus

# Agentic (configure SPECIALIST_MODEL + EVALUATOR_MODEL in .env first)
bash scripts/integration_test_all.sh

# Re-summarize any run folder (no API spend)
python3 tests/baseline_summarize.py <run-dir> --output measurements/<name>-summary.txt
```

LLM non-determinism means re-runs may shift counts by a point or two. The numbers in this folder are from runs on 2026-06-04.
