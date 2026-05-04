from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml
from pydantic import BaseModel, Field, model_validator

from services.gateway.config import Settings, get_settings

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
                "agents registry: no m2m_token for agent "
                f"{aid!r} and agents_m2m_token in gateway.yaml is empty"
            )
        return base, tok


class _RegistryHolder:
    instance: Optional[LoadedAgentsRegistry] = None


def _gateway_root() -> Path:
    return Path(__file__).resolve().parents[1] / "gateway"


def _resolve_registry_path(s: Settings) -> Path:
    raw = (s.agents_registry_path or "").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = _gateway_root() / p
    return p.resolve()


def init_registry_from_settings(s: Settings) -> None:
    """Load the registry YAML at app startup, or disable registry mode."""
    raw = (s.agents_registry_path or "").strip()
    if not raw:
        _RegistryHolder.instance = None
        log.info("agents registry: disabled (set agents_registry_path in gateway.yaml to enable)")
        return
    path = _resolve_registry_path(s)
    if not path.is_file():
        raise FileNotFoundError(
            f"agents_registry_path resolved to {path} but file does not exist"
        )
    with open(path, encoding="utf-8") as f:
        raw_data: Any = yaml.safe_load(f)
    if raw_data is None or not isinstance(raw_data, dict):
        raise ValueError("agents registry: YAML root must be a mapping")
    data = AgentsRegistryFile.model_validate(raw_data)
    _RegistryHolder.instance = LoadedAgentsRegistry(data, s.agents_m2m_token)
    log.info("agents registry: loaded from %s", path)


def resolve_agent_endpoint(action: str, s: Settings) -> tuple[str, str]:
    """Return (base_url_without_trailing_slash, m2m_token)."""
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
            "agents_base_url is not configured in gateway.yaml (or set "
            "agents_registry_path to a valid agents YAML)"
        )
    tok = (s.agents_m2m_token or "").strip()
    if not tok:
        raise ValueError(
            "agents_m2m_token is empty in gateway.yaml; set it for agents auth"
        )
    return base, tok


class AgentsClient:
    def __init__(self) -> None:
        self._client = httpx.Client(timeout=120.0)

    def invoke(
        self,
        action: str,
        payload: dict[str, Any],
        context: Optional[dict[str, Any]] = None,
        trace_id: Optional[str] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "protocol_version": 1,
            "request_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "trace_id": trace_id or f"tr-{int(time.time())}",
            "action": action,
            "payload": payload,
        }
        if context:
            body["context"] = context
        s = get_settings()
        base, token = resolve_agent_endpoint(action, s)
        url = base + "/v1/invoke"
        r = self._client.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self._client.close()


__all__ = [
    "AgentEntry",
    "AgentsClient",
    "AgentsRegistryFile",
    "LoadedAgentsRegistry",
    "PROTOCOL_ACTIONS",
    "init_registry_from_settings",
    "resolve_agent_endpoint",
]
