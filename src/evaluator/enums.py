"""Runtime enum universes consumed by the evaluator's rules validator.

These are frozensets derived from the canonical Literal types in
`src/models/enums.py`. Two layers, two purposes:

  - `src/models/enums.py` owns the Literal types (compile-time vocabulary).
    Cross-cutting; used by Pydantic models, MCP server, audit store.
  - This file owns runtime frozensets that include None where the
    evaluator's per-scenario rules allow null values (e.g.
    primary_tier=None on a no_issue_found scenario), plus tuning
    constants (MID_THRESHOLD, RICH_THRESHOLD) and the short-circuit
    sentinel set that are evaluator-internal.

When adding a new enum value:
  1. Add it to the appropriate Literal in `src/models/enums.py`.
  2. The frozensets below auto-include it via re-derivation.
  3. Update `docs/eval-set.md` enum reference + any composite gold
     answers that should use it.
  4. The rules-validator test in `tests/integration/` catches drift.
"""

from __future__ import annotations

from ..models.enums import (
    ACTION_CATEGORY_VALUES,
    FINDING_TYPE_VALUES,
    TIERS_OR_DEFERRED,
)


# ============================================================
# Enum universes — runtime frozensets, derived from the Literals
# ============================================================
# FINDING_TYPES does not include None because every recommendation has
# a finding_type. The | None on the type is for downstream caller
# convenience (a scoring rule's allowed list type is `list[str | None]`).
FINDING_TYPES: frozenset[str | None] = frozenset(FINDING_TYPE_VALUES)

# PRIMARY_TIERS / SECONDARY_TIERS include None — a no_issue_found
# recommendation has primary_tier=null, and the evaluator's rule
# universe must accept that.
PRIMARY_TIERS: frozenset[str | None] = frozenset(TIERS_OR_DEFERRED) | {None}

SECONDARY_TIERS: frozenset[str | None] = PRIMARY_TIERS

# ACTION_CATEGORIES include None — no_issue_found / diagnostic_deferral
# have null action_category.
ACTION_CATEGORIES: frozenset[str | None] = frozenset(ACTION_CATEGORY_VALUES) | {None}


# ============================================================
# Sentinel set for short-circuit rule (evaluator-internal)
# ============================================================
# When a prediction's finding_type is in this set, score_mid and score_rich
# bypass their per-check logic. Rationale (hallucination prevention): when
# the right answer is "no action", asking the agent to produce keyword-rich
# prose to satisfy a Mid keyword check would invite the model to invent
# action language just to pass the check. Correctness (enum equality) is
# sufficient proof of the right answer for these findings.
NO_ACTION_FINDINGS: frozenset[str] = frozenset({
    "no_issue_found",
    "diagnostic_deferral",
    "insufficient_data",
})


# ============================================================
# Validation helper
# ============================================================
_UNIVERSES: dict[str, frozenset] = {
    "finding_type": FINDING_TYPES,
    "primary_tier": PRIMARY_TIERS,
    "secondary_tier": SECONDARY_TIERS,
    "action_category": ACTION_CATEGORIES,
}


def universe_for(field_name: str) -> frozenset:
    """Return the frozenset of allowed values for the given field name.

    Raises ValueError if the field is not a known enum field.

    Used by the rules validator (rules.py) to confirm every value in a
    rules.json file's *_allowed list is in the corresponding universe.
    """
    if field_name not in _UNIVERSES:
        raise ValueError(
            f"Unknown enum field {field_name!r}. "
            f"Known: {sorted(_UNIVERSES.keys())}"
        )
    return _UNIVERSES[field_name]


def is_valid_value(field_name: str, value) -> bool:
    """Return True if `value` is a member of the enum universe for `field_name`."""
    return value in universe_for(field_name)
