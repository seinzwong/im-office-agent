from __future__ import annotations

import json
from urllib import error, request

from .base import SummaryClientRequest, SummaryClientResponse


class TeammateHTTPSummaryClient:
    def __init__(self, base_url: str, api_key: str = "", timeout_seconds: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def generate(self, req: SummaryClientRequest) -> SummaryClientResponse:
        if not self.base_url:
            raise RuntimeError("TEAMMATE_SUMMARY_BASE_URL is required for http summary client")

        payload = {
            "task_id": req.task_id,
            "message_id": req.message_id,
            "topic_id": req.topic_id,
            "topic_title": req.topic_title,
            "normalized_text": req.normalized_text,
            "plain_text": req.plain_text,
            "importance_score": req.importance_score,
            "has_deliverable": req.has_deliverable,
            "deliverable_signals": req.deliverable_signals,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req_obj = request.Request(
            url=f"{self.base_url}/summary/generate",
            method="POST",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        if self.api_key:
            req_obj.add_header("Authorization", f"Bearer {self.api_key}")

        try:
            with request.urlopen(req_obj, timeout=self.timeout_seconds) as resp:
                resp_body = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"http summary client error: {exc.code} {detail}") from exc
        except Exception as exc:
            raise RuntimeError(f"http summary client request failed: {exc}") from exc

        try:
            data = json.loads(resp_body) if resp_body else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError("http summary client returned non-json response") from exc

        return SummaryClientResponse(
            should_add=bool(data.get("should_add")),
            summary_item=data.get("summary_item"),
            reason=str(data.get("reason", "")),
        )
