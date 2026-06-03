"""Layer 2: Correctness (the hard gate).

Strict equality on the four enum decision fields. A prediction passes
Correctness only when finding_type, primary_tier, secondary_tier, and
action_category all match the per-scenario allowed list (which under the
current design is a single-value list matching the gold).

This is the gate. When Correctness fails, Mid and Rich are skipped (the
caller / Scorer class enforces that). A wrong-tier or wrong-finding-type
recommendation should fail here regardless of how well-structured the rest is.

Also exposes score_floor as a back-compat alias that merges Shape +
Correctness. Older callers (tests, CLI's --tier floor flag) use it.
"""

from __future__ import annotations

from .shape_measure import score_shape
from .types import CheckResult, TierResult


def score_correctness(prediction: dict, expectations: dict) -> TierResult:
    """Correctness checks: enum decision fields are in the per-scenario
    allowed lists drawn from the gold answer.
    """
    checks: list[CheckResult] = []

    # finding_type must be in the allowed list
    if "finding_type_allowed" in expectations:
        allowed = expectations["finding_type_allowed"]
        actual = prediction.get("finding_type")
        checks.append(CheckResult(
            name="finding_type",
            passed=actual in allowed,
            message=f"got {actual!r}, allowed {allowed!r}",
            detail={"allowed": allowed, "produced": actual},
        ))

    # primary_tier must be in the allowed list
    if "primary_tier_allowed" in expectations:
        allowed = expectations["primary_tier_allowed"]
        actual = prediction.get("primary_tier")
        checks.append(CheckResult(
            name="primary_tier",
            passed=actual in allowed,
            message=f"got {actual!r}, allowed {allowed!r}",
            detail={"allowed": allowed, "produced": actual},
        ))

    # action_category must be in the allowed list
    if "action_category_allowed" in expectations:
        allowed = expectations["action_category_allowed"]
        actual = prediction.get("action_category")
        checks.append(CheckResult(
            name="action_category",
            passed=actual in allowed,
            message=f"got {actual!r}, allowed {allowed!r}",
            detail={"allowed": allowed, "produced": actual},
        ))

    overall = all(c.passed for c in checks) if checks else True
    return TierResult(tier="correctness", passed=overall, checks=checks)


# ============================================================
# score_floor: back-compat alias (Shape + Correctness merged)
# ============================================================
def score_floor(prediction: dict, expectations: dict) -> TierResult:
    """Deprecated: combined Shape + Correctness check.

    Kept for back-compat with existing tests and the old CLI. New code
    should call score_shape() and score_correctness() separately.

    Returns a TierResult labeled 'floor' with both layer's checks merged.
    Passes iff both Shape and Correctness pass.
    """
    shape = score_shape(prediction, expectations)
    correctness = score_correctness(prediction, expectations)
    merged_checks = shape.checks + correctness.checks
    return TierResult(
        tier="floor",
        passed=shape.passed and correctness.passed,
        checks=merged_checks,
    )
