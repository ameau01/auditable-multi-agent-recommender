"""Project-wide initialization, configuration, and cleanup.

One place callers go for:

  - `config`   : env var names, default paths, table names. The constants
                 the rest of the codebase reads instead of hard-coding.
  - `init`     : `ensure_env_loaded()`, `get_audit_store()`,
                 `ensure_dataset_cached()`, `require_api_key()`.
                 Idempotent — safe to call from anywhere.
  - `cleanup`  : `wipe_audit_db()`, `wipe_hf_cache()`, `wipe_all()`.
                 Destructive; used by scripts/clean.sh.

Why centralize: before this module, .env was loaded from at least four
sites, the audit DB path lived in src/audit/store.py, the HF cache
path in src/data_loader.py, the LLM provider check in src/evaluator/
judge_client.py. Splitting bootstrap across so many files makes it
hard to answer "what does this project need at runtime?" with one
file open. `src/common/` is that one file.
"""

from __future__ import annotations

from .config import (
    AUDIT_DB_FILE,
    DEFAULT_AUDIT_DB_PATH,
    DEFAULT_HF_CACHE_DIR,
    DATASET_REPO,
    HF_CACHE_ENV,
    AUDIT_DB_ENV,
    project_root,
)
from .init import (
    ensure_dataset_cached,
    ensure_env_loaded,
    get_audit_store,
    llm_provider_status,
    require_api_key,
)
from .cleanup import wipe_all, wipe_audit_db, wipe_hf_cache

__all__ = [
    # config
    "AUDIT_DB_FILE",
    "DEFAULT_AUDIT_DB_PATH",
    "DEFAULT_HF_CACHE_DIR",
    "DATASET_REPO",
    "HF_CACHE_ENV",
    "AUDIT_DB_ENV",
    "project_root",
    # init
    "ensure_dataset_cached",
    "ensure_env_loaded",
    "get_audit_store",
    "llm_provider_status",
    "require_api_key",
    # cleanup
    "wipe_all",
    "wipe_audit_db",
    "wipe_hf_cache",
]
