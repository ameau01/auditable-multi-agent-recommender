"""LLM client wrapper used by tier specialists and the Cross-Tier Evaluator.

Two provider implementations share the `LLMClient` protocol:
  - `AnthropicLLMClient` — wraps langchain-anthropic.
  - `OpenAILLMClient`    — wraps langchain-openai.

Plus the test-only `MockLLMClient` (in `mock_llm.py`).

Per-tier configuration via `.env`:

    SPECIALIST_PROVIDER=anthropic          # or "openai"
    SPECIALIST_MODEL=claude-haiku-4-5-20251001
    EVALUATOR_PROVIDER=anthropic           # or "openai"
    EVALUATOR_MODEL=claude-sonnet-4-6

When the env vars are unset, the defaults are Haiku for specialists and
Sonnet for the Evaluator (the "two LLM tiers" decision in
docs/decisions.md). Both tiers can be independently switched to OpenAI
(e.g. `gpt-4o-mini` for specialists, `gpt-4o` for the Evaluator) by
setting the matching env vars.

The same `.env` may carry an unrelated `LLM_JUDGE_PROVIDER` /
`LLM_JUDGE_MODEL` pair — those are read by `src/evaluator/judge_client.py`
for the eval-set's LLM judge and are independent of the agent system's
clients here.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

# Defaults match the project's principle 6 (model specialization):
# cheap+fast for specialists, capable for synthesis.
DEFAULT_SPECIALIST_PROVIDER = "anthropic"
DEFAULT_SPECIALIST_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_EVALUATOR_PROVIDER = "anthropic"
DEFAULT_EVALUATOR_MODEL = "claude-sonnet-4-6"


def get_specialist_provider() -> str:
    """Lowercased provider name for specialists. 'anthropic' | 'openai'."""
    return os.environ.get(
        "SPECIALIST_PROVIDER", DEFAULT_SPECIALIST_PROVIDER,
    ).lower()


def get_specialist_model() -> str:
    """Model name for specialists."""
    return os.environ.get("SPECIALIST_MODEL", DEFAULT_SPECIALIST_MODEL)


def get_evaluator_provider() -> str:
    """Lowercased provider name for the Cross-Tier Evaluator."""
    return os.environ.get(
        "EVALUATOR_PROVIDER", DEFAULT_EVALUATOR_PROVIDER,
    ).lower()


def get_evaluator_model() -> str:
    """Model name for the Cross-Tier Evaluator."""
    return os.environ.get("EVALUATOR_MODEL", DEFAULT_EVALUATOR_MODEL)


class LLMClient(Protocol):
    """The interface every agent calls into.

    `complete(messages, *, model=None, tools=None)` returns a plain
    dict (`content`, `tool_calls`, `usage`). Schema-agnostic so swapping
    providers is a one-line change in callers.
    """
    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        ...


# ============================================================
# AnthropicLLMClient
# ============================================================
class AnthropicLLMClient:
    """langchain-anthropic backed client.

    The langchain import and the API-key check are deferred until the
    first `complete()` call so that:
      - Unit tests can build the graph (which constructs this client
        with the default factory) without ANTHROPIC_API_KEY set, as long
        as they never actually invoke a specialist or the Evaluator.
      - The same client object can be cheaply held by graph nodes that
        may never be reached on a given cycle (e.g. when the Input
        Harness rejects the trigger and the run short-circuits).
    """

    def __init__(self, default_model: str | None = None) -> None:
        self._default_model = default_model or get_specialist_model()
        self._api_key: str | None = None
        self._chat_cls: Any = None

    def _ensure_ready(self) -> None:
        if self._chat_cls is not None and self._api_key is not None:
            return
        self._api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Add it to .env at the "
                "project root or export it before running."
            )
        from langchain_anthropic import ChatAnthropic  # noqa: PLC0415
        self._chat_cls = ChatAnthropic

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self._ensure_ready()
        # ChatAnthropic accepts `api_key=` at runtime (Pydantic alias for
        # `anthropic_api_key`), but the type stub only declares the
        # latter — silence the call-arg check here.
        chat: Any = self._chat_cls(
            model=model or self._default_model,
            api_key=self._api_key,  # type: ignore[call-arg]
        )
        if tools:
            chat = chat.bind_tools(tools)
        response = chat.invoke(messages)
        return {
            "content": response.content,
            "tool_calls": getattr(response, "tool_calls", []),
            "usage": getattr(response, "usage_metadata", None),
        }


# ============================================================
# OpenAILLMClient
# ============================================================
class OpenAILLMClient:
    """langchain-openai backed client. Mirrors AnthropicLLMClient's shape.

    Same lazy-construction pattern as AnthropicLLMClient — the import
    and the API-key check happen on first `complete()`, not at
    construction, so tests that build the graph without an OpenAI key
    don't trip over the check.
    """

    def __init__(self, default_model: str | None = None) -> None:
        self._default_model = default_model or get_specialist_model()
        self._api_key: str | None = None
        self._chat_cls: Any = None

    def _ensure_ready(self) -> None:
        if self._chat_cls is not None and self._api_key is not None:
            return
        self._api_key = os.environ.get("OPENAI_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to .env or export "
                "it before running."
            )
        # langchain-openai has no published type stubs in the project's
        # mypy environment — install-time optional.
        from langchain_openai import ChatOpenAI  # type: ignore[import-not-found]  # noqa: PLC0415
        self._chat_cls = ChatOpenAI

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self._ensure_ready()
        chat: Any = self._chat_cls(
            model=model or self._default_model,
            api_key=self._api_key,
        )
        if tools:
            chat = chat.bind_tools(tools)
        response = chat.invoke(messages)
        return {
            "content": response.content,
            "tool_calls": getattr(response, "tool_calls", []),
            "usage": getattr(response, "usage_metadata", None),
        }


# ============================================================
# Factory + tier-keyed defaults
# ============================================================
def make_llm_client(provider: str, model: str) -> LLMClient:
    """Construct the right client for the requested provider.

    Anthropic is currently the only supported provider. OpenAI is
    explicitly rejected with a clear error — the `OpenAILLMClient`
    class below stays as a stub for a future re-introduction, but the
    factory refuses to construct it until that re-introduction lands
    with its own test coverage. See docs/decisions.md.

    AnthropicLLMClient raises RuntimeError at construction if
    ANTHROPIC_API_KEY is missing — so any misconfiguration surfaces at
    cycle start rather than mid-cycle on the first LLM call.
    """
    p = provider.lower()
    if p == "anthropic":
        return AnthropicLLMClient(default_model=model)
    if p == "openai":
        raise ValueError(
            "OpenAI provider is currently disabled. Set "
            "SPECIALIST_PROVIDER=anthropic and EVALUATOR_PROVIDER=anthropic "
            "in your .env. (OpenAI was disabled after GPT-5-family API "
            "behavior changes proved hard to track without dedicated test "
            "coverage; see docs/decisions.md.)"
        )
    raise ValueError(
        f"Unknown LLM provider: {provider!r}. Expected 'anthropic'."
    )


def make_specialist_client() -> LLMClient:
    """Construct the specialist client based on env config."""
    return make_llm_client(get_specialist_provider(), get_specialist_model())


def make_evaluator_client() -> LLMClient:
    """Construct the Evaluator client based on env config."""
    return make_llm_client(get_evaluator_provider(), get_evaluator_model())
