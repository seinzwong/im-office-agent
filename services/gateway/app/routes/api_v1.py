from __future__ import annotations

import logging
import subprocess
import time
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
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ...adapter.runtime import lark_cli_path, parse_json_object
from ..config import Settings, get_settings
from ..drive_artifacts import list_artifacts
from ..oauth_tokens import build_feishu_authorize_url, decode_oauth_state, encode_oauth_state, save_user_token
from ..pipelines.delivery import run_deliver_artifacts
from ..pipelines.summary_from_event import run_summary_for_chat

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")
_pending_slides_auth: dict[str, Any] = {}


@router.get("/me")
def me(request: Request) -> dict[str, Any]:
    uid = request.session.get("user_open_id", "anonymous")
    authenticated = uid not in ("anonymous", "dev-open-id")
    return {"user_open_id": uid, "authenticated": authenticated}


@router.post("/auth/dev")
def auth_dev(request: Request) -> dict[str, str]:
    """仅开发：写入会话，不经过飞书 OAuth。"""
    request.session["user_open_id"] = "dev-open-id"
    return {"ok": "true"}


@router.get("/auth/login")
def auth_login(
    request: Request,
    state: str = "",
    user_id: str = "",
    reason: str = "web",
    s: Settings = Depends(get_settings),
) -> Response:
    if not s.lark_app_id:
        return Response(
            content="未配置 LARK_APP_ID。开发环境可 POST /api/v1/auth/dev 登录。",
            media_type="text/plain; charset=utf-8",
        )
    signed_state = state or encode_oauth_state(
        s,
        {
            "user_id": user_id or request.session.get("user_open_id", ""),
            "reason": reason,
            "next": s.public_web_base_url,
        },
    )
    return Response(status_code=302, headers={"Location": build_feishu_authorize_url(s, signed_state)})


@router.get("/auth/callback")
def auth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    s: Settings = Depends(get_settings),
) -> RedirectResponse:
    state_data = decode_oauth_state(s, state)
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
            if j.get("code", 0) != 0:
                raise RuntimeError(f"OAuth exchange failed: code={j.get('code')} msg={j.get('msg')}")
            d = (j or {}).get("data") or {}
            expected_user_id = str(state_data.get("user_id") or "")
            saved = save_user_token(s, d, expected_user_id=expected_user_id)
            oid = d.get("user_id") or d.get("open_id") or "oauth-user"
            if saved.get("ok") and saved.get("user_id"):
                oid = saved["user_id"]
            request.session["user_open_id"] = str(oid)
        except Exception as e:  # noqa: BLE001
            log.warning("OAuth exchange: %s", e)
            request.session["user_open_id"] = "dev-oauth-fallback"
    return RedirectResponse(str(state_data.get("next") or s.public_web_base_url or "/"))


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
) -> Any:
    w = bool(body.deliverables.get("whiteboard", True))
    sl = bool(body.deliverables.get("slides", False))
    if sl:
        auth = _ensure_slides_user_authorization_started()
        if auth is not None:
            return JSONResponse(status_code=409, content=auth)
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


def _ensure_slides_user_authorization_started() -> dict[str, Any] | None:
    cli = lark_cli_path()
    try:
        status_run = subprocess.run(
            [cli, "auth", "status"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("lark-cli auth status failed: %s", exc)
        return {"status": "error", "message": f"无法检查飞书 CLI 授权：{exc}"}

    status_body = parse_json_object(status_run.stdout)
    note = str(status_body.get("note") or "")
    identity = str(status_body.get("identity") or "")
    if status_run.returncode == 0 and "no token" not in note.lower() and identity != "bot":
        return None

    try:
        login_run = subprocess.run(
            [cli, "auth", "login", "--domain", "slides,drive,docs", "--no-wait", "--json"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("lark-cli auth login --no-wait failed: %s", exc)
        return {"status": "error", "message": f"无法发起飞书 CLI 授权：{exc}"}

    login_body = parse_json_object(login_run.stdout)
    verification_url = str(login_body.get("verification_url") or "")
    device_code = str(login_body.get("device_code") or "")
    if login_run.returncode != 0 or not verification_url:
        return {
            "status": "error",
            "message": "飞书 CLI 用户授权缺失，且自动发起授权失败。",
            "stdout": login_run.stdout,
            "stderr": login_run.stderr,
        }

    log.warning("slides user authorization required: %s", verification_url)
    return {
        "status": "auth_required",
        "message": f"生成 PPT 需要重新授权飞书 CLI。请打开授权链接完成授权后，再点击开始生成：{verification_url}",
        "verification_url": verification_url,
        "device_code": device_code,
        "expires_in": login_body.get("expires_in"),
    }


def _ensure_slides_user_authorization_started() -> dict[str, Any] | None:
    cli = lark_cli_path()
    if _slides_user_authorized(cli):
        _pending_slides_auth.clear()
        return None

    pending = _pending_slides_auth_response()
    if pending is not None:
        return pending

    try:
        login_run = subprocess.run(
            [cli, "auth", "login", "--domain", "slides,drive,docs", "--no-wait", "--json"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("lark-cli auth login --no-wait failed: %s", exc)
        return {"status": "error", "message": f"无法发起飞书 CLI 授权：{exc}"}

    login_body = parse_json_object(login_run.stdout)
    verification_url = str(login_body.get("verification_url") or "")
    device_code = str(login_body.get("device_code") or "")
    if login_run.returncode != 0 or not verification_url or not device_code:
        return {
            "status": "error",
            "message": "飞书 CLI 用户授权缺失，且自动发起授权失败。",
            "stdout": login_run.stdout,
            "stderr": login_run.stderr,
        }

    expires_in = int(login_body.get("expires_in") or 600)
    process = subprocess.Popen(
        [cli, "auth", "login", "--device-code", device_code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    _pending_slides_auth.clear()
    _pending_slides_auth.update(
        {
            "verification_url": verification_url,
            "device_code": device_code,
            "expires_at": time.time() + expires_in,
            "process": process,
        }
    )
    log.warning("slides user authorization required: %s", verification_url)
    return _pending_slides_auth_response()


def _slides_user_authorized(cli: str) -> bool:
    try:
        status_run = subprocess.run(
            [cli, "auth", "status"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("lark-cli auth status failed: %s", exc)
        return False
    status_body = parse_json_object(status_run.stdout)
    note = str(status_body.get("note") or "")
    identity = str(status_body.get("identity") or "")
    return status_run.returncode == 0 and "no token" not in note.lower() and identity != "bot"


def _pending_slides_auth_response() -> dict[str, Any] | None:
    if not _pending_slides_auth:
        return None
    expires_at = float(_pending_slides_auth.get("expires_at") or 0)
    process = _pending_slides_auth.get("process")
    if time.time() >= expires_at or (process is not None and process.poll() is not None):
        _pending_slides_auth.clear()
        return None
    verification_url = str(_pending_slides_auth.get("verification_url") or "")
    return {
        "status": "auth_required",
        "message": f"生成 PPT 需要重新授权飞书 CLI。请打开授权链接完成授权，完成后再点击开始生成：{verification_url}",
        "verification_url": verification_url,
        "device_code": _pending_slides_auth.get("device_code"),
        "expires_in": max(0, int(expires_at - time.time())),
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
