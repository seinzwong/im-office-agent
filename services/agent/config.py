from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

IR_SCHEMA_VERSION = "0.2.0"
SUPPORTED_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh"}
REASONING_EFFORT_ALIASES = {"minimal": "low"}


@dataclass(frozen=True)
class AgentSettings:
    provider: str
    base_url: str
    api_key: str
    model: str
    ppt_model: str
    reasoning_effort: str
    timeout_seconds: float = 60.0
    temperature: float = 0.2
    max_tokens: int = 3000


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    env = _load_agent_env()
    return AgentSettings(
        provider=_env_value(env, "AGENT_LLM_PROVIDER"),
        base_url=_env_value(env, "AGENT_LLM_BASE_URL"),
        api_key=_env_value(env, "AGENT_LLM_API_KEY"),
        model=_env_value(env, "AGENT_LLM_MODEL"),
        ppt_model=_env_value(env, "AGENT_PPT_LLM_MODEL", "gpt-5.2"),
        reasoning_effort=_reasoning_effort_value(env, "AGENT_LLM_REASONING_EFFORT", "low"),
        timeout_seconds=float(_env_value(env, "AGENT_LLM_TIMEOUT_SECONDS", "60")),
        temperature=float(_env_value(env, "AGENT_LLM_TEMPERATURE", "0.2")),
        max_tokens=int(_env_value(env, "AGENT_LLM_MAX_TOKENS", "6000")),
    )


def clear_agent_settings_cache() -> None:
    get_agent_settings.cache_clear()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _agent_dir() -> Path:
    return Path(__file__).resolve().parent


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


def _env_value(env: dict[str, str], name: str, default: str = "") -> str:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def _reasoning_effort_value(env: dict[str, str], name: str, default: str = "low") -> str:
    value = _env_value(env, name, default).lower()
    value = REASONING_EFFORT_ALIASES.get(value, value)
    if value in SUPPORTED_REASONING_EFFORTS:
        return value
    return default


__all__ = [
    "AgentSettings",
    "IR_SCHEMA_VERSION",
    "clear_agent_settings_cache",
    "get_agent_settings",
]
