from __future__ import annotations

import time
import uuid
from typing import Any, Optional

import httpx

from ..config import get_settings
from .registry import resolve_agent_endpoint


class AgentsClient:
    def __init__(self) -> None:
        self._client = httpx.Client(timeout=120.0, trust_env=False)

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
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body_preview = (r.text or "").strip()[:2000]
            raise RuntimeError(
                f"Agent HTTP {r.status_code} for {url}: {body_preview}"
            ) from exc
        return r.json()

    def close(self) -> None:
        self._client.close()
