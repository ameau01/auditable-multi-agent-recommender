"""The AnalysisPlan: System Mapper's output, Supervisor's input.

The System Mapper parses the application's terraform + metadata and
emits an AnalysisPlan describing which infrastructure tiers are in
scope and which tier specialists should be invoked. The Supervisor
reads this plan and fans out to the named specialists.

Kept in its own module because it's the protocol between two distinct
agents — both agents import it; neither owns it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import AgentName, Tier


class AnalysisPlan(BaseModel):
    """The System Mapper's analysis plan for one application.

    Strict-shape on purpose. Adding a field requires a deliberate edit
    here and in the corresponding `SystemMapperOutputContent` audit
    payload — keeping the wire shape and the audit content in lockstep.
    """
    application_id: str
    tiers_detected: list[Tier] = Field(default_factory=list)
    specialists_to_invoke: list[AgentName] = Field(default_factory=list)
    terraform_resources_summary: str | None = None
    metadata_summary: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None

    model_config = ConfigDict(extra="forbid")
