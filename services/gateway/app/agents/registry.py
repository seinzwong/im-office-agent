from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, model_validator

from ..config import Settings

log = logging.getLogger(__name__)

PROTOCOL_ACTIONS = frozenset(
    ("summary_from_chat", "deliver_whiteboard", "deliver_slides")
)


class AgentEntry(BaseModel):
    base_url: str = Field(..., min_length=1)
    m2m_token: Optional[str] = None


class AgentsRegistryFile(BaseModel):
    agents: dict[str, AgentEntry]
    routing: dict[str, str]

    @model_validator(mode="after")
    def routing_references_valid(self) -> AgentsRegistryFile:
        for action in PROTOCOL_ACTIONS:
            if action not in self.routing:
                raise ValueError(
                    "agents registry: routing must define all protocol actions; "
                    f"missing: {action}"
                )
            aid = self.routing[action]
            if aid not in self.agents:
                raise ValueError(
                    f"agents registry: routing[{action!r}] references unknown "
                    f"agent_id {aid!r}"
                )
        for aid, entry in self.agents.items():
            if not (entry.base_url or "").strip():
                raise ValueError(f"agents registry: agents[{aid!r}].base_url is empty")
        return self


class LoadedAgentsRegistry:
    def __init__(self, data: AgentsRegistryFile, default_m2m_token: str) -> None:
        self._data = data
        self._default_m2m = (default_m2m_token or "").strip()

    def resolve(self, action: str) -> tuple[str, str]:
        if action not in PROTOCOL_ACTIONS:
            raise ValueError(
                f"Unknown agents action {action!r}; expected one of "
                f"{sorted(PROTOCOL_ACTIONS)}"
            )
        aid = self._data.routing[action]
        entry = self._data.agents[aid]
        base = entry.base_url.strip().rstrip("/")
        tok = (entry.m2m_token or self._default_m2m).strip()
        if not tok:
            raise ValueError(
                f"agents registry: no m2m_token for agent {aid!r} and "
                "AGENTS_M2M_TOKEN is empty"
            )
        return base, tok


class _RegistryHolder:
    instance: Optional[LoadedAgentsRegistry] = None


def _gateway_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_registry_path(s: Settings) -> Path:
    raw = (s.agents_registry_path or "").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = _gateway_root() / p
    return p.resolve()


def init_registry_from_settings(s: Settings) -> None:
    """在应用启动时调用：加载 YAML 或关闭注册表模式。"""
    raw = (s.agents_registry_path or "").strip()
    if not raw:
        _RegistryHolder.instance = None
        log.info("agents registry: disabled (set AGENTS_REGISTRY_PATH to enable)")
        return
    path = _resolve_registry_path(s)
    if not path.is_file():
        raise FileNotFoundError(
            f"AGENTS_REGISTRY_PATH resolved to {path} but file does not exist"
        )
    with open(path, encoding="utf-8") as f:
        raw_data: Any = yaml.safe_load(f)
    if raw_data is None or not isinstance(raw_data, dict):
        raise ValueError("agents registry: YAML root must be a mapping")
    data = AgentsRegistryFile.model_validate(raw_data)
    _RegistryHolder.instance = LoadedAgentsRegistry(data, s.agents_m2m_token)
    log.info("agents registry: loaded from %s", path)


def resolve_agent_endpoint(action: str, s: Settings) -> tuple[str, str]:
    """返回 (base_url_without_trailing_slash, m2m_token)。"""
    if action not in PROTOCOL_ACTIONS:
        raise ValueError(
            f"Unknown agents action {action!r}; expected one of "
            f"{sorted(PROTOCOL_ACTIONS)}"
        )
    reg = _RegistryHolder.instance
    if reg is not None:
        return reg.resolve(action)
    base = (s.agents_base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError(
            "AGENTS_BASE_URL is not configured (or set AGENTS_REGISTRY_PATH to a "
            "valid YAML)"
        )
    tok = (s.agents_m2m_token or "").strip()
    if not tok:
        raise ValueError(
            "AGENTS_M2M_TOKEN is empty; configure M2M token for agents"
        )
    return base, tok
