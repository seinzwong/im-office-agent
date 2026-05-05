from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.responses import Response

from services.agent.agents import generate_ir_from_messages

app = FastAPI(title="IM Office PlanB Agent", version="0.2.0")


@app.get("/healthz")
def healthz() -> Response:
    return _json_response({"ok": True})


@app.post("/agent/generate-ir-from-messages")
def generate_ir_from_messages_endpoint(payload: dict[str, Any]) -> Response:
    return _json_response(generate_ir_from_messages(payload))


def _json_response(payload: dict[str, Any]) -> Response:
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
    )


__all__ = ["app"]
