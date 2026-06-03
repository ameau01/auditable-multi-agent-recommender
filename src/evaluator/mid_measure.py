"""Layer 3: Mid (evidence engagement, scored by LLM judge).

Mid is the lower richness threshold. A prediction passes Mid when the
LLM judge scores its `specific_change` prose at or above MID_THRESHOLD
(currently 30) when compared against the gold's `specific_change`.

The judge call itself is made once per scenario in Scorer.score_one()
and the result is shared between Mid and Rich. This module is a pure
score-interpreter; it does not call the LLM.

Short-circuit rule: when the prediction's finding_type is in
NO_ACTION_FINDINGS, Mid passes automatically with a single 'short_circuit'
check (no judge call needed; there is no action to assess richness on).

Graceful degradation: when no judge_result is provided (e.g., because no
API key is set), Mid returns a single 'skipped' check that reports
passed=True but message="judge unavailable; layer skipped". This keeps
the report-format contract: callers always see four-layer output.
"""

from __future__ import annotations

from typing import Any

from .enums import NO_ACTION_FINDINGS
from .types import CheckResult, TierResult


# ============================================================
# Threshold (single source of truth; tune in one place)
# ============================================================
MID_THRESHOLD = 30


# ============================================================
# Public scorer
# ============================================================
def score_mid(prediction: dict,
              expectations: dict,
              judge_result: dict[str, Any] | None = None) -> TierResult:
    """Score the Mid layer.

    Args:
        prediction: the agent's recommendation dict.
        expectations: the per-scenario rules dict (kept for signature
            compatibility with Shape/Correctness; not consumed here).
        judge_result: {'score': int, 'rationale': str} from the LLM
            judge, or None if the judge could not be called (no API key
            or by caller choice).

    Returns:
        TierResult with one check:
          - 'short_circuit' for no-action findings (passed=True)
          - 'skipped' when judge_result is None (passed=True, marker only)
          - 'judge_richness' otherwise (passed = score >= MID_THRESHOLD)
    """
    # Short-circuit for no-action findings
    finding_type = prediction.get("finding_type")
    if finding_type in NO_ACTION_FINDINGS:
        return TierResult(
            tier="mid",
            passed=True,
            checks=[CheckResult(
                name="short_circuit",
                passed=True,
                message=f"skipped (no-action finding: {finding_type})",
                detail={"finding_type": finding_type, "rule": "NO_ACTION_FINDINGS"},
            )],
        )

    # Graceful degradation: no judge available
    if judge_result is None:
        return TierResult(
            tier="mid",
            passed=True,
            checks=[CheckResult(
                name="skipped",
                passed=True,
                message="judge unavailable; Mid layer skipped",
                detail={"reason": "no judge_result provided (no API key?)"},
            )],
        )

    # Apply the threshold
    score = int(judge_result.get("score", 0))
    rationale = str(judge_result.get("rationale", ""))
    passed = score >= MID_THRESHOLD
    return TierResult(
        tier="mid",
        passed=passed,
        checks=[CheckResult(
            name="judge_richness",
            passed=passed,
            message=(
                f"judge score {score} {'>=' if passed else '<'} "
                f"{MID_THRESHOLD} (Mid threshold)"
            ),
            detail={
                "score": score,
                "threshold": MID_THRESHOLD,
                "rationale": rationale,
            },
        )],
    )
