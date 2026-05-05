from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.responses import Response

from services.agent.agents import (
    generate_artifact_ir_patch,
    update_structuring_summary,
)

app = FastAPI(title="IM Office Agent Service", version="0.1.0")


@app.get("/healthz")
def healthz() -> Response:
    """Return a minimal health response for service probes."""
    return _json_response({"ok": "true"})


@app.post("/agent/update-structuring-summary")
def update_structuring_summary_endpoint(payload: dict[str, Any]) -> Response:
    """HTTP wrapper for structured topic/task summary updates."""
    return _json_response(update_structuring_summary(payload))


@app.post("/agent/generate-ir-patch")
def generate_ir_patch_endpoint(payload: dict[str, Any]) -> Response:
    """HTTP wrapper for Artifact IR patch generation."""
    return _json_response(generate_artifact_ir_patch(payload))


def _json_response(payload: dict[str, Any]) -> Response:
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
    )


__all__ = ["app"]
