from __future__ import annotations

import time
import uuid
from typing import Any, Optional

import httpx

from ..config import get_settings


class AgentsClient:
    def __init__(self) -> None:
        s = get_settings()
        self._url = s.agents_base_url.rstrip("/") + "/v1/invoke"
        self._token = s.agents_m2m_token
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
        r = self._client.post(
            self._url,
            json=body,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self._client.close()
