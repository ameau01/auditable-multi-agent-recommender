"""MockLLMClient: deterministic canned responses for unit tests.

Every test that exercises agent code uses this client. The mock returns
responses indexed by `(agent, call_purpose)`. When a test wants a
specific response, it constructs the mock with the appropriate dict.

The default response is a benign empty `content=""` so tests that
don't exercise LLM-driven branches don't blow up if a node makes a
call they didn't expect.

Counter-intuitively, this module is "real" code (not test code) — it
lives in src/ so notebooks and the runner can inject it too. Real
LLM-driven runs live in tests/judge_live/ or are gated by env vars.
"""

from __future__ import annotations

from typing import Any


_DEFAULT_RESPONSE: dict[str, Any] = {
    "content": "",
    "tool_calls": [],
    "usage": None,
}


class MockLLMClient:
    """Returns canned responses keyed by (agent, call_purpose).

    Construct with a dict mapping `(agent, call_purpose)` tuples to
    response dicts. On `complete()`, the caller passes `agent` and
    `call_purpose` via the messages list's first system message (by
    convention), or the mock falls back to the default response.

    The convention: tests pass `_mock_key` in the first message's
    metadata field. This keeps the production LLM interface clean
    (no test-only kwargs leak into the real client).
    """

    def __init__(
        self,
        responses: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self._responses = responses or {}
        self._call_log: list[dict[str, Any]] = []

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Mock invocation. Records the call for assertion, returns canned."""
        # The first message's _mock_key (if present) selects the canned
        # response. Otherwise the default.
        key: tuple[str, str] | None = None
        if messages and isinstance(messages[0], dict):
            mock_key = messages[0].get("_mock_key")
            if isinstance(mock_key, (list, tuple)) and len(mock_key) == 2:
                key = (str(mock_key[0]), str(mock_key[1]))

        self._call_log.append({
            "messages": messages,
            "model": model,
            "tools": tools,
            "_mock_key": key,
        })

        if key is not None and key in self._responses:
            return self._responses[key]
        return dict(_DEFAULT_RESPONSE)

    @property
    def call_log(self) -> list[dict[str, Any]]:
        """Every call recorded in order. Tests assert on this."""
        return self._call_log
