"""
与 specs/agents-protocol 对齐的本地 Mock，用于 Gateway 联调。

运行：cd mocks/agents && pip install -r requirements.txt && uvicorn main:app --port 18080
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="Mock Agents", version="0.1.0")
TOKEN = os.environ.get("AGENTS_M2M_TOKEN", "dev-m2m-secret")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _ok(req: dict[str, Any], result: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {
        "protocol_version": req.get("protocol_version", 1),
        "request_id": req.get("request_id", str(uuid.uuid4())),
        "ok": True,
        "result": result or {},
    }


def _err(req: dict[str, Any], code: str, msg: str) -> dict[str, Any]:
    return {
        "protocol_version": req.get("protocol_version", 1),
        "request_id": req.get("request_id", ""),
        "ok": False,
        "error": {"code": code, "message": msg},
    }


@app.post("/v1/invoke")
def invoke(
    body: dict[str, Any],
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer")
    t = authorization.split(" ", 1)[-1].strip()
    if t != TOKEN:
        raise HTTPException(403, "bad token")
    act = body.get("action", "")
    pl: dict[str, Any] = body.get("payload") or {}
    if act == "summary_from_chat":
        t0 = int(pl.get("default_window_end_unix", 0))
        return _ok(
            body,
            {
                "time_window": {
                    "start_unix": t0 - 86400,
                    "end_unix": t0,
                    "label": (pl.get("time_hint_text") or "default 24h")[:80],
                },
                "doc_title": "群聊总结（Mock）",
                "doc_content_xml": "<title>群聊总结（Mock）</title><p>Mock Agents 已根据 <code>message_text_aggregated</code> 生成示例正文。</p>",
            },
        )
    if act == "deliver_whiteboard":
        return _ok(
            body,
            {
                "whiteboard_dsl": "mermaid: graph TD; A[Doc] --> B[Whiteboard]",
            },
        )
    if act == "deliver_slides":
        return _ok(
            body,
            {
                "slide_xml_slides": (
                    "<slide><title>Mock 幻灯片</title><body>一页示例</body></slide>"
                ),
            },
        )
    return _err(body, "UNKNOWN_ACTION", f"action={act}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=18080)
