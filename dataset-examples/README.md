# Dataset examples

What the dataset inputs look like. Three scenarios are vendored here so a
reviewer can inspect the shape without setting up the Hugging Face cache.

**Inputs only.** Each scenario folder has telemetry, infrastructure, and
metadata. The gold answer (`target_recommendation`) is deliberately NOT
included here. Gold answers live in [`eval-set/expectations/`](../eval-set/),
so the dataset-examples folder reads like what an agent would see at
inference time: just the inputs, no peek at the right answer.

The full dataset (all 18 scenarios, including gold answers) is on Hugging
Face at
[`ameau01/synthesized-cloud-optimization-recommendations`](https://huggingface.co/datasets/ameau01/synthesized-cloud-optimization-recommendations).

The agent project fetches the full dataset at runtime via
`src/data_loader.py`. The files here are a copy for browsing, not the
canonical source.

## What's here

```
dataset-examples/
├── scenario_02/                     compute / scaling_policy_change (single-tier)
├── scenario_07/                     cache / cache_capacity_adjustment (cross-tier)
├── scenario_08/                     database / query_cache_optimization (cross-tier)
└── README.md                        (this file)
```

Each scenario folder has the same shape:

```
scenario_NN/
├── compute_telemetry.json           per-minute compute tier metrics
├── database_telemetry.json          per-minute database tier metrics
├── cache_telemetry.json             per-minute cache tier metrics
├── network_telemetry.json           per-minute network tier metrics
├── correlation_evidence.json        cross-tier correlation observations
├── main.tf                          Terraform infrastructure spec
└── metadata.json                    scenario narrative + context (gold answer redacted)
```

## Why these three scenarios

The picks showcase the dataset's range across primary tiers, action
categories, and single-tier vs cross-tier reasoning. All three are
`issue_found` scenarios so a reader can see what a rich recommendation
needs to cover.

| Scenario | Primary tier | Action category               | Pairing                       | What the right answer tests                                                  |
|----------|--------------|-------------------------------|-------------------------------|------------------------------------------------------------------------------|
| 02       | compute      | `scaling_policy_change`       | single-tier                   | Pattern reasoning. Spiky load needs scheduled or predictive scaling, not a bigger box. |
| 07       | cache        | `cache_capacity_adjustment`   | cross-tier (cache → database) | Tier collaboration. Cache pressure cascades into database load.              |
| 08       | database     | `query_cache_optimization`    | cross-tier (database → compute) | Tier collaboration. Slow queries cascade into compute waste; the fix is at the DB. |

Three different primary tiers. Three different action categories. One
single-tier scenario and two cross-tier scenarios. A reviewer who reads
all three input sets gets a feel for the dataset's breadth without
browsing 18 scenarios.

The short-circuit scenarios (`no_issue_found` and `diagnostic_deferral`)
are deliberately excluded here. Their gold answers are intentionally
thin because the right answer is "no action" or "defer until more
data." For the full short-circuit story, see
[`docs/eval-set.md`](../docs/eval-set.md).

## What to read next

- For what each scenario's gold answer looks like, see
  [`eval-set/expectations/`](../eval-set/) (e.g., `02.json`, `07.json`,
  `08.json`).
- For how the evaluator scores a recommendation against the gold, see
  [`docs/eval-set.md`](../docs/eval-set.md).
- For sample recommendation reports a reader can read end to end, see
  [`sample_runs/reports/`](../sample_runs/).
- For the high-level architecture this dataset feeds into, see
  [`README.md`](../README.md) and [`ARCHITECTURE.md`](../ARCHITECTURE.md).

## License

MIT. The vendored telemetry is synthesized data released under the dataset's
license (also MIT). See the project root `LICENSE`.
