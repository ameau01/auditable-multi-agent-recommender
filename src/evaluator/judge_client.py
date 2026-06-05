"""LLM judge client for Mid and Rich layers.

Wraps either the Anthropic SDK or the OpenAI SDK to score a prediction's
`specific_change` prose against the gold's `specific_change`. Returns a
structured score (0-100) plus a one-paragraph rationale, via the
provider's structured-output mechanism (Anthropic tool_use, OpenAI
function-calling tool).

Provider selection (in order):
  1. Explicit `LLM_JUDGE_PROVIDER` env var: `anthropic` or `openai`.
  2. Auto-detect from which API key is present. If both keys are
     present, prefer OpenAI.
  3. Raise RuntimeError if neither key is set.

Model selection:
  - `LLM_JUDGE_MODEL` env var overrides the default for the chosen provider.
  - Defaults: claude-haiku-4-5-20251001 (Anthropic), gpt-4o-mini (OpenAI).

Auditability properties (per docs/eval-set.md):
  - Pinned model (per session): the model used is logged on every score
    response and is stable for the duration of the JudgeClient instance.
  - Temperature 0: deterministic-in-practice sampling.
  - Published prompt: src/evaluator/prompts/judge_richness.md.
  - Structured output: the judge returns score + rationale via a single
    required tool call, never free-form text we have to parse.
  - API key loaded from .env via python-dotenv; clear error if missing.

Graceful degradation: when no API key is available,
`JudgeClient.is_available()` returns False and callers should skip Mid
and Rich (or call .score() which raises a clear RuntimeError).

Calibration note: the project's threshold defaults (Mid >= 30, Rich >= 60)
were originally calibrated against Anthropic Haiku and have been
verified against OpenAI gpt-4o-mini as well. Switching providers or
models may shift borderline verdicts; re-run `tests/judge_live/` after
any provider/model change to confirm the gold-vs-gold self-validation
still clears the high-richness band.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


# ============================================================
# Provider + model defaults
# ============================================================
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

TEMPERATURE = 0.0
MAX_TOKENS = 1024

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "judge_richness.md"

# Tool/function name used for the structured score-and-rationale output.
SCORE_TOOL_NAME = "score_richness"

# Shared JSON schema for the structured output (both providers accept
# the same shape, just wrapped differently in their tool/function specs).
_SCORE_PARAMETERS_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "description": (
                "Richness score: 0-29 = low, 30-59 = mid, "
                "60-100 = high (see prompt for criteria)."
            ),
        },
        "rationale": {
            "type": "string",
            "description": (
                "One paragraph explaining the score. Cite specific "
                "phrases from the prediction and gold."
            ),
        },
    },
    "required": ["score", "rationale"],
}

_SCORE_TOOL_DESCRIPTION = (
    "Return the richness score for the prediction's specific_change "
    "prose, on a 0-100 scale, plus a one-paragraph rationale."
)


# ============================================================
# Public client
# ============================================================
class JudgeClient:
    """Stateful judge with one of {Anthropic, OpenAI} under the hood.

    Construct once per evaluator session; reuse across many score() calls.
    The provider, model, and prompt are locked in at __init__.
    """

    def __init__(self,
                 provider: str | None = None,
                 model: str | None = None,
                 prompt_path: Path = PROMPT_PATH):
        """Build a judge client.

        Args:
            provider: Override for LLM_JUDGE_PROVIDER. One of
                'anthropic' or 'openai'. If None, auto-detect from env.
            model: Override for LLM_JUDGE_MODEL. If None, use the default
                for the chosen provider.
            prompt_path: Override for the judge prompt template path.

        Raises:
            RuntimeError: if no usable API key is available for the
                chosen provider.
            FileNotFoundError: if the prompt template is missing.
            ValueError: if provider is set to an unknown value.
        """
        load_dotenv()  # idempotent; safe to call repeatedly

        # Resolve provider and key.
        self._provider = (provider
                          or os.environ.get("LLM_JUDGE_PROVIDER")
                          or _autodetect_provider())
        self._provider = self._provider.lower().strip()

        if self._provider == "anthropic":
            self._api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not self._api_key:
                raise RuntimeError(
                    "Provider 'anthropic' requested but ANTHROPIC_API_KEY "
                    "is not set. Add it to .env or set LLM_JUDGE_PROVIDER=openai."
                )
            self._model = (model
                           or os.environ.get("LLM_JUDGE_MODEL")
                           or DEFAULT_ANTHROPIC_MODEL)
        elif self._provider == "openai":
            # OpenAI is currently disabled. The `_call_openai` path below
            # stays as a stub for a future re-introduction, but the
            # constructor refuses to wire it up until that lands with
            # its own test coverage. See docs/decisions.md.
            raise ValueError(
                "OpenAI judge provider is currently disabled. Set "
                "LLM_JUDGE_PROVIDER=anthropic in your .env. (OpenAI was "
                "disabled after GPT-5-family API behavior changes proved "
                "hard to track without dedicated test coverage.)"
            )
        else:
            raise ValueError(
                f"Unknown LLM judge provider {self._provider!r}. "
                f"Expected 'anthropic' or 'openai'."
            )

        if not prompt_path.exists():
            raise FileNotFoundError(f"Judge prompt not found: {prompt_path}")
        self._prompt_template = prompt_path.read_text()

        # Lazy-import the SDK actually needed; keeps the dependency
        # footprint thin if only one provider is installed. The client
        # is typed as Any because the two SDKs (Anthropic, OpenAI) have
        # no shared protocol mypy can narrow on. Per-provider call paths
        # branch in _call_anthropic / _call_openai below.
        self._client: Any
        if self._provider == "anthropic":
            from anthropic import Anthropic
            self._client = Anthropic(api_key=self._api_key)
        else:  # openai
            from openai import OpenAI
            self._client = OpenAI(api_key=self._api_key)

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------
    @staticmethod
    def is_available() -> bool:
        """True if a usable provider key is set.

        Returns True if either ANTHROPIC_API_KEY or OPENAI_API_KEY is in
        the environment. Callers should use this before constructing the
        judge to decide whether to skip Mid/Rich gracefully or fail loud.
        """
        load_dotenv()
        return bool(os.environ.get("ANTHROPIC_API_KEY")
                    or os.environ.get("OPENAI_API_KEY"))

    def score(self, gold: dict, prediction: dict) -> dict[str, Any]:
        """Score the prediction's specific_change against the gold.

        Args:
            gold: full gold answer dict (must include `specific_change`).
            prediction: full prediction dict (must include `specific_change`).

        Returns:
            {'score': int (0-100), 'rationale': str, 'provider': str, 'model': str}.

        Raises:
            ValueError: if either input is missing `specific_change`.
            RuntimeError: if the judge does not return a tool call.
        """
        gold_text = gold.get("specific_change")
        pred_text = prediction.get("specific_change")
        if not isinstance(gold_text, str) or not gold_text.strip():
            raise ValueError("gold is missing a non-empty `specific_change`")
        if not isinstance(pred_text, str) or not pred_text.strip():
            raise ValueError(
                "prediction is missing a non-empty `specific_change`"
            )

        rendered_prompt = self._prompt_template.replace(
            "{gold_specific_change}", gold_text
        ).replace(
            "{prediction_specific_change}", pred_text
        )

        if self._provider == "anthropic":
            score, rationale = self._call_anthropic(rendered_prompt)
        else:
            score, rationale = self._call_openai(rendered_prompt)

        return {
            "score": score,
            "rationale": rationale,
            "provider": self._provider,
            "model": self._model,
        }

    # --------------------------------------------------------
    # Introspection (useful for logging + tests)
    # --------------------------------------------------------
    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def temperature(self) -> float:
        return TEMPERATURE

    # --------------------------------------------------------
    # Private: per-provider call paths
    # --------------------------------------------------------
    def _call_anthropic(self, rendered_prompt: str) -> tuple[int, str]:
        tool_schema = {
            "name": SCORE_TOOL_NAME,
            "description": _SCORE_TOOL_DESCRIPTION,
            "input_schema": _SCORE_PARAMETERS_SCHEMA,
        }
        response = self._client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": SCORE_TOOL_NAME},
            messages=[{"role": "user", "content": rendered_prompt}],
        )
        for block in response.content:
            if (getattr(block, "type", None) == "tool_use"
                    and block.name == SCORE_TOOL_NAME):
                return int(block.input["score"]), str(block.input["rationale"])
        raise RuntimeError(
            f"Anthropic judge did not return a {SCORE_TOOL_NAME} tool call. "
            f"Response: {response.content!r}"
        )

    def _call_openai(self, rendered_prompt: str) -> tuple[int, str]:
        function_schema = {
            "type": "function",
            "function": {
                "name": SCORE_TOOL_NAME,
                "description": _SCORE_TOOL_DESCRIPTION,
                "parameters": _SCORE_PARAMETERS_SCHEMA,
            },
        }
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            tools=[function_schema],
            tool_choice={"type": "function",
                         "function": {"name": SCORE_TOOL_NAME}},
            messages=[{"role": "user", "content": rendered_prompt}],
        )
        tool_calls = response.choices[0].message.tool_calls or []
        for call in tool_calls:
            if call.function.name == SCORE_TOOL_NAME:
                import json
                args = json.loads(call.function.arguments)
                return int(args["score"]), str(args["rationale"])
        raise RuntimeError(
            f"OpenAI judge did not return a {SCORE_TOOL_NAME} function call. "
            f"Response: {response!r}"
        )


# ============================================================
# Internal helpers
# ============================================================
def _autodetect_provider() -> str:
    """Pick a provider based on which API key is present.

    Prefers OpenAI if both keys are set. Override the preference with
    LLM_JUDGE_PROVIDER=anthropic (in .env) or by passing
    provider='anthropic' to JudgeClient(). Raises RuntimeError if
    neither key is set.
    """
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    raise RuntimeError(
        "No LLM judge API key found. Set OPENAI_API_KEY or ANTHROPIC_API_KEY "
        "in .env, or pass provider= explicitly."
    )
