"""Project-wide constants and path helpers.

Constants here are the single source of truth for anything that
multiple modules currently hard-code (DB filename, default cache
location, env var names). Adding a new constant: prefer this file
unless the constant is purely local to one module.
"""

from __future__ import annotations

from pathlib import Path


# ============================================================
# Project root resolution
# ============================================================
def project_root() -> Path:
    """Return the absolute path to the project root.

    `src/common/config.py` is two dirs deep from the project root.
    Resolving via `__file__` works in source tree, installed mode,
    and editable installs (uv sync's editable layout).
    """
    return Path(__file__).resolve().parent.parent.parent


# ============================================================
# Audit DB
# ============================================================
# The file the audit store writes to. Relative paths anchor to project
# root; AUDIT_DB_ENV overrides if set in env or .env.
AUDIT_DB_FILE: str = "audit.db"
DEFAULT_AUDIT_DB_DIR: str = ".audit_db"
DEFAULT_AUDIT_DB_PATH: str = f"{DEFAULT_AUDIT_DB_DIR}/{AUDIT_DB_FILE}"
AUDIT_DB_ENV: str = "AUDIT_DB_PATH"

# In-memory SQLite sentinel — re-exported from src/audit/store.py so
# callers can do `from src.common import IN_MEMORY`. Imported lazily
# to avoid an import cycle with the audit package.


# ============================================================
# Hugging Face dataset cache
# ============================================================
DATASET_REPO: str = "ameau01/synthesized-cloud-optimization-recommendations"
DEFAULT_HF_CACHE_DIR: str = ".hf_cache"
HF_CACHE_ENV: str = "HF_HOME"
# DATASET_REVISION pins to a specific commit. None = follow main.
DATASET_REVISION: str | None = None


# ============================================================
# LLM provider env vars (just the names — no key values ever live here)
# ============================================================
ANTHROPIC_KEY_ENV: str = "ANTHROPIC_API_KEY"
OPENAI_KEY_ENV: str = "OPENAI_API_KEY"
LLM_PROVIDER_ENV: str = "LLM_JUDGE_PROVIDER"   # "anthropic" | "openai"
LLM_MODEL_ENV: str = "LLM_JUDGE_MODEL"          # override default model

# Agent-specific model knobs (read by src/agents/llm_client.py).
SPECIALIST_MODEL_ENV: str = "SPECIALIST_MODEL"
EVALUATOR_MODEL_ENV: str = "EVALUATOR_MODEL"


# ============================================================
# LangSmith env vars (langchain reads these directly)
# ============================================================
LANGSMITH_API_KEY_ENV: str = "LANGSMITH_API_KEY"
LANGSMITH_PROJECT_ENV: str = "LANGSMITH_PROJECT"
LANGSMITH_TRACING_ENV: str = "LANGCHAIN_TRACING_V2"
LANGSMITH_ENDPOINT_ENV: str = "LANGSMITH_ENDPOINT"


# ============================================================
# Audit table names (audit/schema.py uses these names; constants live
# here so a tool that only wants to read tables doesn't need to import
# schema.py)
# ============================================================
AUDIT_TABLE_AUDIT_RECORDS: str = "audit_records"
AUDIT_TABLE_HARNESS_TRAIL: str = "harness_trail"
AUDIT_TABLE_OPERATIONS: str = "operations"

ALL_AUDIT_TABLES: tuple[str, ...] = (
    AUDIT_TABLE_AUDIT_RECORDS,
    AUDIT_TABLE_HARNESS_TRAIL,
    AUDIT_TABLE_OPERATIONS,
)


# ============================================================
# Path resolvers (apply project-root anchoring + env override)
# ============================================================
def audit_db_path(explicit: str | None = None) -> Path:
    """Resolve the audit DB file path.

    Precedence:
      1. explicit arg (used by tests passing IN_MEMORY).
      2. AUDIT_DB_ENV from env / .env.
      3. DEFAULT_AUDIT_DB_PATH anchored to project root.

    The ':memory:' sentinel passes through unchanged so AuditStore
    can detect it. Otherwise the parent directory is created if missing.
    """
    import os
    if explicit is not None:
        raw = explicit
    else:
        raw = os.environ.get(AUDIT_DB_ENV, DEFAULT_AUDIT_DB_PATH)
    if raw == ":memory:":
        return Path(raw)
    p = Path(raw)
    if not p.is_absolute():
        p = project_root() / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def hf_cache_path() -> Path:
    """Resolve the HF cache directory path.

    HF_HOME from env if set; otherwise project_root()/.hf_cache.
    The directory is NOT created here — huggingface_hub does that on
    first download.
    """
    import os
    raw = os.environ.get(HF_CACHE_ENV, DEFAULT_HF_CACHE_DIR)
    p = Path(raw)
    if not p.is_absolute():
        p = project_root() / p
    return p
