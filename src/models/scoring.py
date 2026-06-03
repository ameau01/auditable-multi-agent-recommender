"""Pydantic models the evaluator emits.

Three model groups:
  - CheckResult / TierResult: per-layer scoring atoms returned by the
    score_shape / score_correctness / score_mid / score_rich helpers.
    Migrated from dataclass (formerly src/evaluator/types.py) so they
    serialize via model_dump() and gain JSON Schema for free.
  - JudgeResult: the LLM judge's structured verdict for the richness
    layer.
  - ScoreOneResult: the full five-layer scorecard returned by
    Scorer.score_one(). Each layer is either a TierResult or the
    literal string "skipped" (when correctness fails and Mid/Rich are
    gated off).

All models are strict (extra='forbid'); these are internal evaluator
outputs and any unexpected key indicates a bug.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


_StrictConfig = ConfigDict(extra="forbid")


class CheckResult(BaseModel):
    """One scoring check within a layer. The layer passes only if every
    check passed."""
    name: str
    passed: bool
    message: str = ""
    detail: dict[str, Any] = {}

    model_config = _StrictConfig


class TierResult(BaseModel):
    """The outcome of one scoring layer (shape / correctness / floor /
    mid / rich). passed is True only when every check passed."""
    tier: str
    passed: bool
    checks: list[CheckResult]

    model_config = _StrictConfig


class JudgeResult(BaseModel):
    """LLM judge's structured verdict on richness. Returned by
    JudgeClient.score(). The score is 0-100 with the rubric documented
    in src/evaluator/prompts/judge_richness.md."""
    score: int
    rationale: str
    provider: str   # 'anthropic' or 'openai'
    model: str      # e.g. 'claude-sonnet-4-5-20250929' or 'gpt-4o-2024-11-20'

    model_config = _StrictConfig


# Layer payload: either a real TierResult or the literal "skipped"
# (when correctness fails and Mid/Rich are gated off).
LayerResult = TierResult | Literal["skipped"]


class ScoreOneResult(BaseModel):
    """Full scorecard from Scorer.score_one(). Five layers, in fixed
    order. The 'floor' layer is a back-compat alias for the union of
    'shape' and 'correctness' (kept for callers that still consume the
    old 3-layer shape)."""
    shape: LayerResult
    correctness: LayerResult
    floor: LayerResult
    mid: LayerResult
    rich: LayerResult

    model_config = _StrictConfig
