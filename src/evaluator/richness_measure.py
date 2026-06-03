"""Layer 4: Rich (orchestrated synthesis, LLM judge + structural checks).

Rich is the higher richness threshold. A prediction passes Rich when:
  1. The LLM judge scores its `specific_change` prose at or above
     RICH_THRESHOLD (currently 60), AND
  2. The supporting fields pass four deterministic completeness checks:
       - fixture_citation
       - cost_impact_quantified
       - projected_state_quantified
       - evidence_structured

The judge call is made once per scenario in Scorer.score_one() and the
result is shared between Mid and Rich. This module accepts the judge
result as an argument; it does not call the LLM itself.

Short-circuit rule: when the prediction's finding_type is in
NO_ACTION_FINDINGS, Rich passes automatically with a 'short_circuit'
marker. The judge is not consulted; the structural checks are not run.

Graceful degradation: when no judge_result is provided (e.g., because no
API key is set), Rich returns a 'skipped' marker rather than running.
This keeps the four-layer report format intact whether or not the judge
is available.
"""

from __future__ import annotations

from typing import Any

from .scoring_helpers import prediction_text
from .enums import NO_ACTION_FINDINGS
from .types import CheckResult, TierResult


# ============================================================
# Threshold (single source of truth; tune in one place)
# ============================================================
RICH_THRESHOLD = 60


# ============================================================
# Public scorer
# ============================================================
def score_rich(prediction: dict,
               expectations: dict,
               scenario_metadata: dict | None = None,
               judge_result: dict[str, Any] | None = None) -> TierResult:
    """Score the Rich layer.

    Args:
        prediction: the agent's recommendation dict.
        expectations: the per-scenario rules dict (consumed for
            must_cite_fixture).
        scenario_metadata: the per-scenario metadata dict (consumed for
            scenario_specific_evidence used by the fixture_citation check).
        judge_result: {'score': int, 'rationale': str} from the LLM
            judge, or None if the judge could not be called.

    Returns:
        TierResult. When Rich runs, the first check is 'judge_richness'
        (the gate); the remaining four are the structural completeness
        checks. Rich passes only when all five pass.
    """
    # Short-circuit for no-action findings
    finding_type = prediction.get("finding_type")
    if finding_type in NO_ACTION_FINDINGS:
        return TierResult(
            tier="rich",
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
            tier="rich",
            passed=True,
            checks=[CheckResult(
                name="skipped",
                passed=True,
                message="judge unavailable; Rich layer skipped",
                detail={"reason": "no judge_result provided (no API key?)"},
            )],
        )

    # Judge gate: must clear RICH_THRESHOLD before structural checks run
    score = int(judge_result.get("score", 0))
    rationale = str(judge_result.get("rationale", ""))
    gate_passed = score >= RICH_THRESHOLD
    gate_check = CheckResult(
        name="judge_richness",
        passed=gate_passed,
        message=(
            f"judge score {score} {'>=' if gate_passed else '<'} "
            f"{RICH_THRESHOLD} (Rich threshold)"
        ),
        detail={
            "score": score,
            "threshold": RICH_THRESHOLD,
            "rationale": rationale,
        },
    )

    # If the gate fails, return early. The structural checks are about
    # validating supporting fields for a prose that already cleared the
    # richness bar; running them on thin prose is wasted signal.
    if not gate_passed:
        return TierResult(tier="rich", passed=False, checks=[gate_check])

    # Structural checks (deterministic; unchanged from prior versions)
    checks: list[CheckResult] = [gate_check]
    text = prediction_text(prediction)

    # fixture_citation: must cite at least one identifier from a named fixture
    if "must_cite_fixture" in expectations and scenario_metadata is not None:
        fixture_name = expectations["must_cite_fixture"]
        evidence = scenario_metadata.get("scenario_specific_evidence", {}) or {}
        items = evidence.get(fixture_name, []) or []
        identifiers = _extract_fixture_identifiers(fixture_name, items)
        if not identifiers:
            checks.append(CheckResult(
                name="fixture_citation",
                passed=True,
                message=f"skipped, {fixture_name} is empty in metadata",
            ))
        else:
            cited = [i for i in identifiers if i.lower() in text]
            checks.append(CheckResult(
                name="fixture_citation",
                passed=len(cited) >= 1,
                message=(
                    f"cited {len(cited)}/{len(identifiers)} {fixture_name} "
                    f"identifiers (need >=1)"
                ),
                detail={
                    "fixture": fixture_name,
                    "identifiers": identifiers,
                    "cited": cited,
                },
            ))

    # Quantification checks apply when the agent proposes a concrete change.
    action_cat = prediction.get("action_category")
    quantification_applies = action_cat not in ("sla_review", None)

    # cost_impact_quantified
    cost_impact = prediction.get("cost_impact") or {}
    has_cost_number = False
    if isinstance(cost_impact, dict):
        for k in ("savings_monthly_usd", "current_monthly_usd",
                  "projected_monthly_usd", "savings_pct"):
            v = cost_impact.get(k)
            if isinstance(v, (int, float)) and v != 0:
                has_cost_number = True
                break
    if quantification_applies:
        checks.append(CheckResult(
            name="cost_impact_quantified",
            passed=has_cost_number,
            message=f"cost_impact has numeric fields: {has_cost_number}",
            detail={"cost_impact": cost_impact},
        ))
    else:
        checks.append(CheckResult(
            name="cost_impact_quantified",
            passed=True,
            message=f"skipped, does not apply to action_category={action_cat}",
        ))

    # projected_state_quantified
    proj = prediction.get("projected_state") or {}
    has_proj_number = False
    if isinstance(proj, dict):
        for k, v in proj.items():
            if isinstance(v, (int, float)):
                has_proj_number = True
                break
    if quantification_applies:
        checks.append(CheckResult(
            name="projected_state_quantified",
            passed=has_proj_number,
            message=f"projected_state has numeric fields: {has_proj_number}",
            detail={"projected_state": proj},
        ))
    else:
        checks.append(CheckResult(
            name="projected_state_quantified",
            passed=True,
            message=f"skipped, does not apply to action_category={action_cat}",
        ))

    # evidence_structured: total bullets across the three evidence categories
    ev = prediction.get("evidence") or {}
    n_bullets = 0
    if isinstance(ev, dict):
        for cat in ("telemetry_observations", "infrastructure_context",
                    "correlation_observations"):
            bullets = ev.get(cat) or []
            if isinstance(bullets, list):
                n_bullets += len(bullets)
    checks.append(CheckResult(
        name="evidence_structured",
        passed=n_bullets >= 3,
        message=f"evidence bullets total: {n_bullets} (need >=3)",
        detail={"bullets": n_bullets},
    ))

    overall = all(c.passed for c in checks)
    return TierResult(tier="rich", passed=overall, checks=checks)


# ============================================================
# Helper specific to Rich's fixture_citation check
# ============================================================
def _extract_fixture_identifiers(fixture_name: str, items: list) -> list[str]:
    """Pull identifier strings out of one fixture's entries.

    Known gap: top_queries entries shaped {query_text, count, p95_latency_ms}
    return empty (no recognized identifier key). Scenario 08 hits this gap;
    a test pins the behavior so it can't drift silently.
    """
    out: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            if isinstance(item, str):
                out.append(item)
            continue
        if fixture_name == "top_queries":
            for key in ("name", "shorthand", "query_name", "id"):
                if key in item and isinstance(item[key], str):
                    out.append(item[key])
                    break
        elif fixture_name == "top_cache_keys":
            for key in ("pattern", "key", "name"):
                if key in item and isinstance(item[key], str):
                    out.append(item[key])
                    break
        elif fixture_name == "per_instance_breakdown":
            if "instance_id" in item:
                out.append(item["instance_id"])
    return out
