# scoring_rules

Per-scenario check parameters. One folder per scenario id, each holding a
single `rules.json` file.

```
scoring_rules/
├── 01/rules.json
├── 02/rules.json
├── ...
└── 18/rules.json
```

Each `rules.json` tells the four-layer evaluator what counts as a
correct, mid-tier-rich, and rich-tier-rich answer for that one scenario.
The matching gold answer lives at [`../expectations/NN.json`](../expectations/).

For the loader, validator, and short-circuit predicate, see
[`src/evaluator/rules.py`](../../src/evaluator/rules.py).

## `rules.json` schema

A rules file is a flat JSON object with the following keys. Required
keys must be present in every scenario. Optional keys appear when the
scenario needs them.

### Required

| Key                       | Type            | What it does                                                                                                                            |
|---------------------------|-----------------|------------------------------------------------------------------------------------------------------------------------------------------|
| `description`             | string          | Human-readable explanation of the scenario and why this is the right answer. Not used by the scorer; documentation only.                |
| `finding_type_allowed`    | list of strings | Allowed values for the prediction's `finding_type` field. Strict enum equality at Correctness. One-element list = single right answer.  |
| `primary_tier_allowed`    | list (strings or `null`) | Same shape for `primary_tier`. May include `null` (for `no_issue_found`) or `"deferred"` (for `diagnostic_deferral`).                   |
| `secondary_tier_allowed`  | list (strings or `null`) | Same shape for `secondary_tier`. `null` for single-tier scenarios.                                                                     |
| `action_category_allowed` | list (strings or `null`) | Same shape for `action_category`. `null` for no-action findings.                                                                       |

Validation. Every value in every `*_allowed` list must appear in the
matching enum universe defined in `src/evaluator/enums.py`. Drift fails
loud at load time (`ValueError`).

### Optional

| Key                        | Type                | When to include                                                                                                            |
|----------------------------|---------------------|----------------------------------------------------------------------------------------------------------------------------|
| `action_category_rationale`| string              | Notes on why one category was chosen over defensible alternatives. Documentation only.                                     |
| `action_keyword_groups`    | list of lists of strings | Mid layer. Each inner list is an OR-group; the prose must contain at least one substring from at least N groups (case-insensitive). |
| `action_keyword_min_match` | int                 | Mid layer. The N in "at least N groups." Defaults vary by scenario; typically 2.                                            |
| `multi_tier_evidence`      | object              | Mid layer. `{ "must_cite_tiers": [...], "min_tiers": N }`. The prose must mention at least N of the named tiers.            |
| `must_cite_fixture`        | string              | Rich layer. The scenario-metadata fixture name (e.g. `"top_queries"`) whose identifiers the prose must cite.                |
| `short_circuit`            | object              | Marker that Mid + Rich are bypassed for this scenario. See below.                                                          |

### `short_circuit` block

Used by scenarios whose `finding_type` is in `NO_ACTION_FINDINGS`
(`no_issue_found`, `diagnostic_deferral`, `insufficient_data`). When
present and `applies: true`, the Mid and Rich measures return a single
`short_circuit` check rather than running keyword, multi-tier, fixture,
or quantification checks.

```json
"short_circuit": {
  "applies": true,
  "reason": "Why Mid and Rich are bypassed for this scenario."
}
```

The bypass logic lives in the Mid and Rich measure modules; the
sentinel set is `NO_ACTION_FINDINGS` in `src/evaluator/enums.py`. The
`reason` field is documentation only.

## Worked example

Scenario 08 (`database` → `compute`, `query_cache_optimization`):

```json
{
  "description": "Slow DB queries cascade into elevated compute latency...",
  "finding_type_allowed": ["issue_found"],
  "primary_tier_allowed": ["database"],
  "secondary_tier_allowed": ["compute"],
  "action_category_allowed": ["query_cache_optimization"],
  "action_keyword_groups": [
    ["optimize", "slow query", "query"],
    ["read replica", "replica", "follower"],
    ["compute", "application latency", "downstream", "cascade"]
  ],
  "action_keyword_min_match": 2,
  "multi_tier_evidence": {
    "must_cite_tiers": ["database", "compute"],
    "min_tiers": 2
  },
  "must_cite_fixture": "top_queries"
}
```

A prediction passes all four layers when:

- Shape: well-formed JSON with the expected top-level fields.
- Correctness: the four enum decision fields equal the four `*_allowed`
  values exactly.
- Mid: the prose contains a substring from at least 2 of the 3 keyword
  groups AND mentions both `database` and `compute` (`min_tiers=2`).
- Rich: the prose cites at least one identifier from the `top_queries`
  fixture (when recognized; see the limitations note in
  [`docs/eval-set.md`](../../docs/eval-set.md)), and `cost_impact` plus
  `projected_state` carry numeric values.

## Authoring a new scenario rules file

1. Pick the gold answer's four enum fields. Each `*_allowed` list gets
   one value, matching the gold exactly.
2. If the finding type is in `NO_ACTION_FINDINGS`, add a `short_circuit`
   block and stop. No keyword groups or fixture citations needed.
3. Otherwise, pick `action_keyword_groups` based on what vocabulary an
   agent would naturally use to describe the right action. Aim for 2 to
   3 groups; require at least 2 to match.
4. If the right answer crosses tiers, add `multi_tier_evidence` with the
   tier names the prose must mention.
5. If the prediction should cite a scenario-metadata fixture (a named
   query, a specific cache key, an instance id), set
   `must_cite_fixture` to the fixture key.
6. Run the integration tests to confirm the matching gold answer passes
   every layer:

```bash
pytest tests/integration/test_golden_answers.py -v -k scenario_NN
```

If the gold doesn't pass, either the rules or the gold need to change.
The two are designed to agree.

## See also

- [`../expectations/`](../expectations/): gold answers (`NN.json`).
- [`../../docs/eval-set.md`](../../docs/eval-set.md): four-layer scoring spec.
- [`../../src/evaluator/rules.py`](../../src/evaluator/rules.py): loader and validator.
- [`../../src/evaluator/enums.py`](../../src/evaluator/enums.py): enum universes and `NO_ACTION_FINDINGS`.
