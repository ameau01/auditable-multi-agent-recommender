"""Cloud-optimization eval-set: four-layer scoring (Shape, Correctness, Mid, Rich).

Public API:
    from src.evaluator import Scorer

    # Bulk: load all 18 rules from eval-set/, optionally with metadata
    s = Scorer.from_eval_set_dir("eval-set/", dataset_examples_dir="dataset-examples/")
    result = s.score_one("08", prediction_dict)
    all_results = s.score_all({"01": pred01, "02": pred02, ...})

    # Single scenario: load one rules.json on demand
    s = Scorer.from_single_rules_file("path/to/rules.json", sid="99")

The class is named `Scorer` rather than `Evaluator` so it does not clash
with the Cross-Tier Evaluator agent. One scores a finished Composite; the
other produces the Composite. Different sides of the pipeline.

For stateless scoring (no pre-loaded state, take expectations as a parameter),
import directly from the layer modules or the back-compat facade:

    from src.evaluator.tiers import score_shape, score_correctness, score_mid, score_rich
    from src.evaluator.enums import NO_ACTION_FINDINGS, FINDING_TYPES, ...
"""

from .evaluator import Scorer
from .enums import (
    FINDING_TYPES, PRIMARY_TIERS, SECONDARY_TIERS, ACTION_CATEGORIES,
    NO_ACTION_FINDINGS,
)

__all__ = [
    "Scorer",
    "FINDING_TYPES", "PRIMARY_TIERS", "SECONDARY_TIERS", "ACTION_CATEGORIES",
    "NO_ACTION_FINDINGS",
]
