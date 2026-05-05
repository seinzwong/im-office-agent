from __future__ import annotations

import logging
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

from ..config import Settings, get_settings
from ..drive_artifacts import list_artifacts
from ..pipelines.delivery import run_deliver_artifacts
from ..pipelines.summary_from_event import run_summary_for_chat

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")


@router.get("/me")
def me(request: Request) -> dict[str, Any]:
    uid = request.session.get("user_open_id", "anonymous")
    return {"user_open_id": uid, "authenticated": uid != "anonymous"}


@router.post("/auth/dev")
def auth_dev(request: Request) -> dict[str, str]:
    """仅开发：写入会话，不经过飞书 OAuth。"""
    request.session["user_open_id"] = "dev-open-id"
    return {"ok": "true"}


@router.get("/auth/login")
def auth_login(s: Settings = Depends(get_settings)) -> Response:
    if not s.lark_app_id:
        return Response(
            content="未配置 LARK_APP_ID。开发环境可 POST /api/v1/auth/dev 登录。",
            media_type="text/plain; charset=utf-8",
        )
    params = {
        "app_id": s.lark_app_id,
        "redirect_uri": s.oauth_redirect_uri,
        "state": "x",
    }
    u = s.lark_base_url + "/open-apis/authen/v1/authorize?" + urllib.parse.urlencode(
        {**params, "response_type": "code", "scope": "openid contact:user.base:readonly"}
    )
    return Response(status_code=302, headers={"Location": u})


@router.get("/auth/callback")
def auth_callback(
    request: Request,
    code: str = "",
    s: Settings = Depends(get_settings),
) -> dict[str, str]:
    if s.lark_app_id and code:
        # 使用官方接口换 user_access_token（SaaS 侧可能不同，MVP 占位）
        try:
            u = s.lark_base_url + "/open-apis/authen/v1/access_token"
            r = httpx.post(
                u,
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "app_id": s.lark_app_id,
                    "app_secret": s.lark_app_secret,
                },
                timeout=30.0,
            )
            r.raise_for_status()
            j = r.json()
            d = (j or {}).get("data") or {}
            oid = d.get("user_id") or d.get("open_id") or "oauth-user"
            request.session["user_open_id"] = str(oid)
        except Exception as e:  # noqa: BLE001
            log.warning("OAuth exchange: %s", e)
            request.session["user_open_id"] = "dev-oauth-fallback"
    return {"ok": "true"}


@router.get("/artifacts")
def list_artifacts_route() -> dict[str, Any]:
    return {"artifacts": list_artifacts()}


class DeliverIn(BaseModel):
    file_tokens: list[str] = Field(..., min_length=1)
    deliverables: dict[str, bool] = Field(
        default_factory=lambda: {"whiteboard": True, "slides": False}
    )


@router.post("/deliver", status_code=status.HTTP_202_ACCEPTED)
def deliver(
    body: DeliverIn,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    w = bool(body.deliverables.get("whiteboard", True))
    sl = bool(body.deliverables.get("slides", False))
    if not w and not sl:
        raise HTTPException(400, "请至少选择画板或 PPT 之一")
    log.info("deliver accepted file_count=%s whiteboard=%s slides=%s", len(body.file_tokens), w, sl)
    background_tasks.add_task(
        run_deliver_artifacts,
        body.file_tokens,
        w,
        sl,
    )
    return {
        "status": "accepted",
        "message": "已提交生成，请稍后在飞书该目录中刷新本页以查看新文件。",
    }


class JssdkIn(BaseModel):
    url: str = ""


@router.post("/jssdk/config")
def jssdk_config(
    p: JssdkIn, s: Settings = Depends(get_settings)
) -> dict[str, Any]:
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
    aggregate_text: str = "示例：讨论 A 与 讨论 B 两条消息"


@router.post("/dev/trigger-summary")
def dev_trigger(body: DevTriggerIn) -> dict[str, str]:
    t0 = body.t0_unix
    if t0 is None:
        import time

        t0 = int(time.time())
    d = run_summary_for_chat(
        body.chat_id,
        body.time_hint,
        t0,
        body.aggregate_text,
        "dev-synthetic",
    )
    if not d:
        return {"id": "failed", "url": "about:blank", "file_token": ""}
    if d.get("ok") is False:
        return {
            "id": "failed",
            "url": d.get("open_url", "about:blank") or "about:blank",
            "file_token": d.get("file_token", "") or "",
        }
    return {
        "id": d.get("file_token", "summary") or d.get("title", "summary")[:32],
        "url": d.get("open_url", "about:blank") or "about:blank",
        "file_token": d.get("file_token", "") or "",
    }
