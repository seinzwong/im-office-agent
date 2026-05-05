from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


AGENT_LLM_PROVIDER = "deepseek"
AGENT_LLM_BASE_URL = "https://api.deepseek.com"
AGENT_LLM_API_KEY = "YOUR_API_KEY_HERE"
AGENT_LLM_MODEL = "deepseek-v4-pro"
AGENT_LLM_MOCK_MODE = False
AGENT_IR_SCHEMA_VERSION = "0.2.0"
AGENT_OUTPUT_DIR = "services/agent/output"


@dataclass(frozen=True)
class AgentSettings:
    """Runtime settings for the agent service."""

    provider: str
    base_url: str
    api_key: str
    model: str
    mock_mode: bool
    ir_schema_version: str
    output_dir: Path
    timeout_seconds: float = 60.0


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    """Load agent settings from .env files and environment variables."""
    env = _load_agent_env()
    return AgentSettings(
        provider=_env_value(env, "AGENT_LLM_PROVIDER", AGENT_LLM_PROVIDER),
        base_url=_env_value(env, "AGENT_LLM_BASE_URL", AGENT_LLM_BASE_URL),
        api_key=_env_value(env, "AGENT_LLM_API_KEY", AGENT_LLM_API_KEY),
        model=_env_value(env, "AGENT_LLM_MODEL", AGENT_LLM_MODEL),
        mock_mode=_env_bool(env, "AGENT_LLM_MOCK_MODE", AGENT_LLM_MOCK_MODE),
        ir_schema_version=AGENT_IR_SCHEMA_VERSION,
        output_dir=_resolve_output_dir(_env_value(env, "AGENT_OUTPUT_DIR", AGENT_OUTPUT_DIR)),
        timeout_seconds=float(_env_value(env, "AGENT_LLM_TIMEOUT_SECONDS", "60")),
    )


def clear_agent_settings_cache() -> None:
    """Clear cached settings after changing environment variables in-process."""
    get_agent_settings.cache_clear()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _agent_dir() -> Path:
    return Path(__file__).resolve().parent


def _resolve_output_dir(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = _repo_root() / path
    return path.resolve()


def _load_agent_env() -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in (_repo_root() / ".env", _agent_dir() / ".env"):
        merged.update(_parse_dotenv(path))
    for key, value in os.environ.items():
        if key.startswith("AGENT_") and value.strip():
            merged[key] = value.strip()
    return merged


def _parse_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1].replace('\\"', '"')
        elif value.startswith("'") and value.endswith("'") and len(value) >= 2:
            value = value[1:-1]
        out[key] = value
    return out


def _env_value(env: dict[str, str], name: str, default: str) -> str:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def _env_bool(env: dict[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "AGENT_IR_SCHEMA_VERSION",
    "AGENT_LLM_API_KEY",
    "AGENT_LLM_BASE_URL",
    "AGENT_LLM_MOCK_MODE",
    "AGENT_LLM_MODEL",
    "AGENT_LLM_PROVIDER",
    "AGENT_OUTPUT_DIR",
    "AgentSettings",
    "clear_agent_settings_cache",
    "get_agent_settings",
]
