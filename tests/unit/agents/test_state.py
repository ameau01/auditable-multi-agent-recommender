"""Tests for the LangGraph state schema.

Covers:
  - Required fields can't be omitted.
  - Defaults for optional collections.
  - extra='forbid' rejects unknown fields.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agents.state import CycleState


def test_minimal_state_is_valid() -> None:
    s = CycleState(application_id="app-08", cycle_id="cycle_x")
    assert s.application_id == "app-08"
    assert s.cycle_id == "cycle_x"
    # Defaults
    assert s.input_validation_passed is False
    assert s.analysis_plan is None
    assert s.specialists_invoked == []
    assert s.specialist_findings == []
    assert s.evaluator_record is None
    assert s.recommendation is None
    assert s.terminal_state is None
    assert s.failure_reason is None


def test_missing_required_field_raises() -> None:
    with pytest.raises(ValidationError):
        CycleState(application_id="app-08")  # type: ignore[call-arg]


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        CycleState(
            application_id="app-08",
            cycle_id="cycle_x",
            mystery_field=1,  # type: ignore[call-arg]
        )


def test_partial_update_round_trip() -> None:
    """LangGraph merges partial updates back into the state. Confirm the
    Pydantic model supports a model_dump() round-trip without losing fields."""
    s = CycleState(application_id="app-08", cycle_id="cycle_x",
                    specialists_invoked=["compute_analyst"])
    dumped = s.model_dump()
    s2 = CycleState.model_validate(dumped)
    assert s2 == s
