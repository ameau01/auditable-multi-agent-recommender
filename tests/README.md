# tests/

Two categories: fast unit tests organized by module under `tests/unit/`,
end-to-end integration tests against real benchmark data under
`tests/integration/`.

## What's here

```
tests/
├── unit/                                       fast unit tests (default run)
│   ├── conftest.py                             shared sys.path setup
│   ├── evaluator/                              mirrors src/evaluator/
│   │   ├── test_shape_measure.py               score_shape
│   │   ├── test_correctness_measure.py         score_correctness + score_floor
│   │   ├── test_mid_measure.py                 score_mid + prediction_text helper
│   │   ├── test_richness_measure.py            score_rich + fixture extraction
│   │   ├── test_enums.py                       enum universes + helpers
│   │   ├── test_rules.py                       rules loader + validator (tmp_path)
│   │   ├── test_evaluator.py                   Evaluator class (direct constructor)
│   │   └── test_tiers.py                       facade + score_all_tiers
│   └── agents/                                 mirrors src/agents/
│       └── test_orchestrator.py                orchestrate() stub contract
├── integration/                                end-to-end against real data
│   ├── conftest.py
│   ├── fixtures/
│   │   └── mock_predictions/                   4 JSON mocks for edge-case tests
│   ├── test_eval_set_data.py                   gold answers well-formed (data)
│   ├── test_golden_answers.py                  every gold passes its own evaluator
│   ├── test_edge_cases.py                      bad mocks fail expected layer
│   └── test_eval_cli.py                        CLI end-to-end (subprocess)
├── run_all_tests.py                            default: unit only (fast)
└── README.md                                   (this file)
```

## Unit vs integration: what's the line?

| Concern | Unit | Integration |
|---|---|---|
| Scope | One function/class in isolation | End-to-end pipeline or multiple components |
| Inputs | Crafted dicts, `tmp_path` fixtures | Real files in `eval-set/`, real subprocesses |
| Speed | Microseconds per test | Milliseconds to seconds per test |
| Filesystem | Mocked or `tmp_path` only | Reads real benchmark folders |

By this line:
- **CLI tests** (`test_eval_cli.py`) live in `tests/integration/` because they spawn a real Python subprocess and read the real `eval-set/` folder.
- **`test_rules.py`** and **`test_evaluator.py`** live in `tests/unit/` because they use `tmp_path` fixtures or direct in-memory constructors. They never touch the real `eval-set/`.
- **`test_eval_set_data.py`** lives in `tests/integration/` because it validates the actual 18 gold answers and the actual 18 scoring rules files.

## Running tests

```bash
# Default: unit only (fast, ~0.1s)
python tests/run_all_tests.py

# Unit + integration
python tests/run_all_tests.py --all

# Integration only
python tests/run_all_tests.py --integration-only

# Direct pytest, full control
python -m pytest tests/                   # everything
python -m pytest tests/unit/              # unit only
python -m pytest tests/integration/       # integration only
python -m pytest tests/unit/evaluator/    # just the evaluator unit tests
```

Helper scripts in `scripts/` wrap common combinations:

```bash
scripts/run_golden.sh         # gold-answer validation (one integration test file)
scripts/run_integration.sh    # all integration tests
scripts/run_demo.sh           # eval-set/demo_scoring.py
```

## What each category tests

### Unit (`tests/unit/`)

Each test file mirrors one source module. Tests use crafted inputs and
isolated fixtures so a failure pinpoints one function's behavior.

| File | Tests | Source module |
|------|-------|---------------|
| `evaluator/test_shape_measure.py` | 6 | `src/evaluator/shape_measure.py` |
| `evaluator/test_correctness_measure.py` | 8 | `src/evaluator/correctness_measure.py` |
| `evaluator/test_mid_measure.py` | 10 | `src/evaluator/mid_measure.py` (+ scoring_helpers) |
| `evaluator/test_richness_measure.py` | 9 | `src/evaluator/richness_measure.py` |
| `evaluator/test_enums.py` | 11 | `src/evaluator/enums.py` |
| `evaluator/test_rules.py` | 13 | `src/evaluator/rules.py` |
| `evaluator/test_evaluator.py` | 10 | `src/evaluator/evaluator.py` (Evaluator class) |
| `evaluator/test_tiers.py` | 2 | `src/evaluator/tiers.py` (facade) |
| `agents/test_orchestrator.py` | 2 | `src/agents/orchestrator.py` |

### Integration (`tests/integration/`)

Tests the scorer + benchmark data + CLI together. Reads real gold
answers, real scoring rules, real mock prediction JSON files, spawns a
real CLI subprocess.

| File | Tests | Concern |
|------|-------|---------|
| `test_eval_set_data.py` | 207 | Gold answers + rules well-formedness; enum validation against the universe |
| `test_golden_answers.py` | 94 | Every gold passes every layer of its own evaluator |
| `test_edge_cases.py` | 13 | Each bad mock fails the expected layer |
| `test_eval_cli.py` | 12 | CLI works end-to-end (subprocess + real files) |

## What `tests/run_all_tests.py` does

A small wrapper around pytest that defaults to `tests/unit/` only. Pass
`--all` or `--integration` to include integration. Pass `--integration-only`
to skip unit entirely. Extra args forward to pytest.

The fast default keeps the test-edit loop tight during src/ development.
Run the integration suite explicitly before publishing changes to the
gold answers or scoring rules.

## Where things are NOT

- **Dataset-shape tests** for `dataset-examples/` no longer exist. Per
  the project's "no code in dataset-examples" rule, the dataset-examples
  folder holds pure inputs only.
- **Helper scripts** (verify_trace.py, etc.) live in `scripts/`, not
  here.

Today's count: **403 tests pass**.
