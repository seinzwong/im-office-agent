from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any, Optional

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .context_hygiene.context_packet_builder import run_deliver_artifacts
from .context_hygiene.topic_summary_service import run_summary_for_chat
from .event_gateway.feishu_event_handler import router as feishu_event_router
from .raw_timeline.raw_timeline_service import list_artifacts
from .structuring_router import router as structuring_router

log = logging.getLogger(__name__)
api_router = APIRouter(prefix="/api/v1")


@api_router.get("/me")
def me(request: Request) -> dict[str, Any]:
    uid = request.session.get("user_open_id", "anonymous")
    return {"user_open_id": uid, "authenticated": uid != "anonymous"}


@api_router.post("/auth/dev")
def auth_dev(request: Request) -> dict[str, str]:
    request.session["user_open_id"] = "dev-open-id"
    return {"ok": "true"}


@api_router.get("/auth/login")
def auth_login(s: Settings = Depends(get_settings)) -> Response:
    if not s.lark_app_id:
        return Response(
            content="LARK_APP_ID is not configured. Use POST /api/v1/auth/dev in development.",
            media_type="text/plain; charset=utf-8",
        )
    params = {
        "app_id": s.lark_app_id,
        "redirect_uri": s.oauth_redirect_uri,
        "state": "x",
    }
    url = s.lark_base_url + "/open-apis/authen/v1/authorize?" + urllib.parse.urlencode(
        {**params, "response_type": "code", "scope": "openid contact:user.base:readonly"}
    )
    return Response(status_code=302, headers={"Location": url})


@api_router.get("/auth/callback")
def auth_callback(
    request: Request,
    code: str = "",
    s: Settings = Depends(get_settings),
) -> dict[str, str]:
    if s.lark_app_id and code:
        try:
            url = s.lark_base_url + "/open-apis/authen/v1/access_token"
            response = httpx.post(
                url,
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "app_id": s.lark_app_id,
                    "app_secret": s.lark_app_secret,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = (response.json() or {}).get("data") or {}
            user_id = data.get("user_id") or data.get("open_id") or "oauth-user"
            request.session["user_open_id"] = str(user_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("OAuth exchange failed: %s", exc)
            request.session["user_open_id"] = "dev-oauth-fallback"
    return {"ok": "true"}


@api_router.get("/artifacts")
def list_artifacts_route() -> dict[str, Any]:
    return {"artifacts": list_artifacts()}


class DeliverIn(BaseModel):
    file_tokens: list[str] = Field(..., min_length=1)
    deliverables: dict[str, bool] = Field(
        default_factory=lambda: {"whiteboard": True, "slides": False}
    )


@api_router.post("/deliver", status_code=status.HTTP_202_ACCEPTED)
def deliver(body: DeliverIn, background_tasks: BackgroundTasks) -> dict[str, str]:
    want_whiteboard = bool(body.deliverables.get("whiteboard", True))
    want_slides = bool(body.deliverables.get("slides", False))
    if not want_whiteboard and not want_slides:
        raise HTTPException(400, "Select at least one deliverable: whiteboard or slides")
    background_tasks.add_task(
        run_deliver_artifacts,
        body.file_tokens,
        want_whiteboard,
        want_slides,
    )
    return {"status": "accepted", "message": "Delivery task accepted"}


class JssdkIn(BaseModel):
    url: str = ""


@api_router.post("/jssdk/config")
def jssdk_config(p: JssdkIn, s: Settings = Depends(get_settings)) -> dict[str, Any]:
    url = (p.url or "")[:2000]
    if not s.lark_app_id:
        return {
            "appId": "dev",
            "timestamp": "1",
            "nonceStr": "n",
            "signature": "dev",
            "url": url,
        }
    return {
        "appId": s.lark_app_id,
        "timestamp": "0",
        "nonceStr": "jssdk",
        "signature": "see_feishu_jsapi",
        "url": url,
    }


class DevTriggerIn(BaseModel):
    chat_id: str = "oc_dev"
    time_hint: str = ""
    t0_unix: Optional[int] = None
    aggregate_text: str = "Summarize project A and project B discussion."


@api_router.post("/dev/trigger-summary")
def dev_trigger(body: DevTriggerIn) -> dict[str, str]:
    t0 = body.t0_unix or int(time.time())
    result = run_summary_for_chat(
        body.chat_id,
        body.time_hint,
        t0,
        body.aggregate_text,
        "dev-synthetic",
    )
    if not result:
        return {"id": "failed", "url": "about:blank", "file_token": ""}
    if result.get("ok") is False:
        return {
            "id": "failed",
            "url": result.get("open_url", "about:blank") or "about:blank",
            "file_token": result.get("file_token", "") or "",
        }
    return {
        "id": result.get("file_token", "summary") or result.get("title", "summary")[:32],
        "url": result.get("open_url", "about:blank") or "about:blank",
        "file_token": result.get("file_token", "") or "",
    }


router = APIRouter()
router.include_router(api_router)
router.include_router(structuring_router)
router.include_router(feishu_event_router)

__all__ = ["api_router", "router"]
