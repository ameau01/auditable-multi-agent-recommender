"""LLM client wrapper used by tier specialists and the Cross-Tier Evaluator.

Two implementations share the `LLMClient` protocol:

  - `AnthropicLLMClient` (this module) — production. Uses
    `langchain-anthropic` under the hood so LangGraph and LangSmith
    auto-instrument calls. Reads SPECIALIST_MODEL / EVALUATOR_MODEL
    from environment so model choice stays config, not code.
  - `MockLLMClient` (in `mock_llm.py`) — tests. Returns canned
    responses indexed by `(agent, call_purpose)`.

Phase 11a (the agent skeleton) does NOT call the LLM. The System Mapper and
Supervisor work from code-only logic. This module exists so that
Phase 11b's first specialist can be wired with a real client without
introducing the interface mid-build.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

# Model choice is config, not code. Defaults match the project's
# principle 6 (model specialization): cheap+fast for specialists,
# capable for synthesis.
DEFAULT_SPECIALIST_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_EVALUATOR_MODEL = "claude-sonnet-4-6"


def get_specialist_model() -> str:
    """The model name tier specialists should use. Override via env."""
    return os.environ.get("SPECIALIST_MODEL", DEFAULT_SPECIALIST_MODEL)


def get_evaluator_model() -> str:
    """The model name the Cross-Tier Evaluator should use. Override via env."""
    return os.environ.get("EVALUATOR_MODEL", DEFAULT_EVALUATOR_MODEL)


class LLMClient(Protocol):
    """The interface every agent calls into.

    `complete(messages, *, model=None, tools=None)` returns the model's
    structured response. The shape of the response is intentionally a
    plain dict at this layer — each agent post-validates against its
    own Pydantic schema. Keeping the LLM-client layer schema-agnostic
    means swapping providers (Anthropic, OpenAI) is a one-line change
    in callers.
    """
    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        ...


class AnthropicLLMClient:
    """Production client. Lazy-imports langchain-anthropic so this
    module loads even when langchain isn't installed (unit tests use
    MockLLMClient and never touch this class).

    Phase 11a does not exercise this class. Phase 11b lights it up
    when the first specialist's ReAct loop lands.
    """

    def __init__(self, default_model: str | None = None) -> None:
        self._default_model = default_model or get_specialist_model()
        # Lazy import keeps the import graph clean for unit tests.
        from langchain_anthropic import ChatAnthropic  # noqa: PLC0415
        self._chat_cls = ChatAnthropic

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Call the model with messages + optional tools.

        Returns a dict with the raw response shape from the langchain
        AIMessage (so callers see `content`, `tool_calls`, `usage`).
        """
        chat: Any = self._chat_cls(model=model or self._default_model)
        if tools:
            chat = chat.bind_tools(tools)
        response = chat.invoke(messages)
        return {
            "content": response.content,
            "tool_calls": getattr(response, "tool_calls", []),
            "usage": getattr(response, "usage_metadata", None),
        }
