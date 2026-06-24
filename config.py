"""Configuration loader.

Reads config.local.py first (user-specific, gitignored),
then falls back to environment variables,
then uses defaults.
"""

from __future__ import annotations

import os
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent

# Defaults
_DEFAULT_ROOTS = [
    str(_BASE_DIR / "samples"),
]
_DEFAULT_INDEX = str(_BASE_DIR / "data" / "samples_index.jsonl")
_DEFAULT_SUMMARY = str(_BASE_DIR / "data" / "samples_summary.json")
_DEFAULT_LA_HOST = "127.0.0.1"
_DEFAULT_LA_PORT = 8765


def _load_local():
    """Load config.local.py if it exists."""
    local_path = _BASE_DIR / "config.local.py"
    if local_path.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("config_local", local_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    return None


_local = _load_local()


def get_samples_roots() -> list[str]:
    if _local and hasattr(_local, "SAMPLES_ROOTS") and _local.SAMPLES_ROOTS:
        return _local.SAMPLES_ROOTS
    env = os.environ.get("SAMPLES_PATH")
    if env:
        return [env]
    return list(_DEFAULT_ROOTS)


def get_index_path() -> str:
    if _local and hasattr(_local, "INDEX_PATH"):
        p = _local.INDEX_PATH
        if not os.path.isabs(p):
            p = str(_BASE_DIR / p)
        return p
    return _DEFAULT_INDEX


def get_summary_path() -> str:
    if _local and hasattr(_local, "SUMMARY_PATH"):
        p = _local.SUMMARY_PATH
        if not os.path.isabs(p):
            p = str(_BASE_DIR / p)
        return p
    return _DEFAULT_SUMMARY


def get_db_path() -> str:
    """SQLite database path (default: data/samples.db)."""
    return os.environ.get(
        "SAMPLE_LIBRARIAN_DB",
        str(_BASE_DIR / "data" / "samples.db"),
    )


def get_liveagent_host() -> str:
    if _local and hasattr(_local, "LIVEAGENT_HOST"):
        return _local.LIVEAGENT_HOST
    return os.environ.get("LIVEAGENT_HOST", _DEFAULT_LA_HOST)


def get_liveagent_port() -> int:
    if _local and hasattr(_local, "LIVEAGENT_PORT"):
        return _local.LIVEAGENT_PORT
    return int(os.environ.get("LIVEAGENT_PORT", str(_DEFAULT_LA_PORT)))
