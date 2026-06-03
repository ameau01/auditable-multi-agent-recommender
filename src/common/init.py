"""Idempotent initialization helpers.

Every function here is safe to call multiple times. They lazy-import
heavy deps (huggingface_hub, anthropic, openai) so importing this
module is cheap even when the deps aren't needed for the caller.

Public surface:

  ensure_env_loaded()
    Calls `dotenv.load_dotenv()` exactly once per process. Subsequent
    calls are no-ops. Centralizes what used to be four separate
    `load_dotenv()` calls scattered across the codebase.

  get_audit_store(db_path=None)
    Returns an AuditStore with the schema already initialized.
    Equivalent to `AuditStore(db_path); store.initialize()`. Use this
    in scripts and notebooks; tests can still construct AuditStore
    directly when they need fine control.

  ensure_dataset_cached(force_download=False)
    Triggers `huggingface_hub.snapshot_download` for the project's
    dataset if the local cache is missing or `force_download=True`.
    Returns the local cache path. No-op on subsequent calls.

  llm_provider_status() -> dict
    Tell-me-what's-configured introspection. Returns
    {provider: "anthropic"|"openai"|None, anthropic_key_set: bool,
     openai_key_set: bool, model: str, source: "env"|"default"|"explicit"}.
    Used by scripts to print a friendly diagnostic before agent runs.

  require_api_key(provider)
    Raise RuntimeError if the provider's key isn't set. Callers that
    *need* an LLM call to succeed use this to fail fast with a clear
    message rather than mid-call with a SDK error.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from . import config

if TYPE_CHECKING:
    from ..audit.store import AuditStore


_env_loaded = False


# ============================================================
# .env loading
# ============================================================
def ensure_env_loaded() -> None:
    """Load .env into os.environ exactly once per process.

    Safe to call from anywhere. Subsequent calls are no-ops, avoiding
    the cost of re-reading the .env file on every audit-store
    construction.
    """
    global _env_loaded
    if _env_loaded:
        return
    # Lazy import — dotenv is light but the module-level pattern keeps
    # all heavy imports lazy for consistency.
    from dotenv import load_dotenv  # noqa: PLC0415
    load_dotenv()
    _env_loaded = True


# ============================================================
# Audit DB
# ============================================================
def get_audit_store(db_path: str | None = None) -> "AuditStore":
    """Return an AuditStore with the schema initialized.

    Args:
        db_path: explicit path override. None = use AUDIT_DB_PATH env
                 or the project default (.audit_db/audit.db).

    Calling pattern this replaces:

        from src.audit import AuditStore
        store = AuditStore(db_path=...)
        store.initialize()

    Now:

        from src.common import get_audit_store
        store = get_audit_store()           # production default
        store = get_audit_store(IN_MEMORY)  # tests

    The function is idempotent in that calling it twice with the same
    path returns two AuditStore instances pointing at the same DB; the
    `initialize()` calls are CREATE TABLE IF NOT EXISTS so no harm.
    """
    ensure_env_loaded()
    from ..audit.store import AuditStore  # noqa: PLC0415
    store = AuditStore(db_path=db_path)
    store.initialize()
    return store


# ============================================================
# HF dataset
# ============================================================
def ensure_dataset_cached(force_download: bool = False) -> Path:
    """Ensure the project's HF dataset is present locally.

    Args:
        force_download: if True, re-download even if the cache appears
                        complete. Use for resetting after a corrupt cache.

    Returns the local cache directory path. First call downloads
    (~12 MB). Subsequent calls are no-ops via huggingface_hub's own
    local-files-only short-circuit.

    Raises RuntimeError when the download fails (network unreachable,
    rate-limited, etc.) and there is no usable local cache to fall back
    to.
    """
    ensure_env_loaded()
    # Lazy import to keep src/common cheap.
    from huggingface_hub import snapshot_download  # noqa: PLC0415

    cache_dir = config.hf_cache_path()
    cache_dir.mkdir(parents=True, exist_ok=True)
    # HF respects HF_HOME if set; we point it at our cache.
    os.environ.setdefault(config.HF_CACHE_ENV, str(cache_dir))

    try:
        local_path = snapshot_download(
            repo_id=config.DATASET_REPO,
            repo_type="dataset",
            revision=config.DATASET_REVISION,
            cache_dir=str(cache_dir),
            # `force_download=True` re-fetches even when files exist.
            # Pass it through directly; snapshot_download handles the
            # "force" semantics internally.
            force_download=force_download,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Could not fetch dataset {config.DATASET_REPO!r} into "
            f"{cache_dir}: {exc}. If you are offline and have an "
            "existing cache, set HF_HUB_OFFLINE=1 to skip the network "
            "check and reuse local files only."
        ) from exc
    return Path(local_path)


# ============================================================
# LLM provider introspection
# ============================================================
def llm_provider_status() -> dict[str, object]:
    """Return what the LLM provider chooser sees right now.

    Used by scripts to print a friendly diagnostic before starting an
    agent run. Does NOT raise; missing keys show up as `False` in the
    `*_key_set` fields, and the caller decides whether that's OK.

    Returned dict shape:
      provider:         "anthropic" | "openai" | None  (None = neither key)
      anthropic_key_set: bool
      openai_key_set:    bool
      model:             str | None  (the model that would be used)
      source:            "env_explicit" | "env_auto" | None
                         (explicit = LLM_JUDGE_PROVIDER set; auto = picked
                          based on which key is present)
    """
    ensure_env_loaded()

    anthropic_set = bool(os.environ.get(config.ANTHROPIC_KEY_ENV))
    openai_set = bool(os.environ.get(config.OPENAI_KEY_ENV))
    explicit = os.environ.get(config.LLM_PROVIDER_ENV)

    provider: str | None
    source: str | None
    if explicit in ("anthropic", "openai"):
        provider = explicit
        source = "env_explicit"
    elif openai_set:
        provider = "openai"
        source = "env_auto"
    elif anthropic_set:
        provider = "anthropic"
        source = "env_auto"
    else:
        provider = None
        source = None

    # Default models match src/evaluator/judge_client.py defaults.
    default_models = {
        "anthropic": "claude-haiku-4-5-20251001",
        "openai":    "gpt-4o-mini",
    }
    model_override = os.environ.get(config.LLM_MODEL_ENV)
    if provider:
        model = model_override or default_models.get(provider)
    else:
        model = None

    return {
        "provider":          provider,
        "anthropic_key_set": anthropic_set,
        "openai_key_set":    openai_set,
        "model":             model,
        "source":            source,
    }


def require_api_key(provider: str) -> None:
    """Raise RuntimeError if the provider's API key isn't in env.

    Use at the start of any code path that *must* call an LLM, so the
    failure surfaces with a clear message instead of a SDK auth error
    mid-call.
    """
    ensure_env_loaded()
    env_name = {
        "anthropic": config.ANTHROPIC_KEY_ENV,
        "openai":    config.OPENAI_KEY_ENV,
    }.get(provider)
    if env_name is None:
        raise ValueError(f"unknown LLM provider: {provider!r}")
    if not os.environ.get(env_name):
        raise RuntimeError(
            f"{provider} requires {env_name} in env (or .env). "
            f"Either set it, or use a different provider via "
            f"{config.LLM_PROVIDER_ENV}=<anthropic|openai>."
        )
