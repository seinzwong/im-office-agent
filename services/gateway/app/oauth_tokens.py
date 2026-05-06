from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from itsdangerous import BadSignature, URLSafeSerializer

from .config import Settings

log = logging.getLogger(__name__)

DEFAULT_USER_SCOPES = (
    "contact:user.base:readonly",
    "offline_access",
    "docx:document:readonly",
    "docx:document:create",
    "docx:document:write_only",
    "drive:file:upload",
    "space:document:retrieve",
)
TOKEN_REFRESH_SKEW_SECONDS = 300


def build_oauth_login_url(
    settings: Settings,
    user_id: str = "",
    reason: str = "summary",
    next_url: str = "",
) -> str:
    state = encode_oauth_state(settings, {"user_id": user_id, "reason": reason, "next": next_url})
    base = (settings.gateway_public_base_url or "").rstrip("/")
    if base:
        return f"{base}/api/v1/auth/login?{_urlencode({'state': state})}"
    return build_feishu_authorize_url(settings, state)


def build_feishu_authorize_url(settings: Settings, state: str) -> str:
    params = {
        "app_id": settings.lark_app_id,
        "redirect_uri": settings.oauth_redirect_uri,
        "response_type": "code",
        "scope": _oauth_scope(settings),
        "state": state,
    }
    return f"{_api_base(settings)}/open-apis/authen/v1/authorize?{_urlencode(params)}"


def encode_oauth_state(settings: Settings, payload: dict[str, Any]) -> str:
    data = {
        "user_id": str(payload.get("user_id") or ""),
        "reason": str(payload.get("reason") or ""),
        "next": str(payload.get("next") or ""),
        "ts": int(time.time()),
    }
    return _serializer(settings).dumps(data)


def decode_oauth_state(settings: Settings, value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = _serializer(settings).loads(value)
    except BadSignature:
        log.warning("invalid oauth state signature")
        return {}
    return data if isinstance(data, dict) else {}


def save_user_token(settings: Settings, token_payload: dict[str, Any], expected_user_id: str = "") -> dict[str, Any]:
    record = _record_from_token_payload(token_payload, expected_user_id)
    aliases = [alias for alias in record.get("aliases", []) if alias]
    if not aliases:
        return {"ok": False, "error": "OAuth response did not include a user id."}
    store = _read_store(settings)
    users = store.setdefault("users", {})
    for alias in aliases:
        users[alias] = record
    _write_store(settings, store)
    return {"ok": True, "user_id": aliases[0], "aliases": aliases}


def get_valid_user_access_token(settings: Settings, user_id: str) -> str:
    uid = (user_id or "").strip()
    if not uid:
        return ""
    store = _read_store(settings)
    record = _record_for_user(store, uid)
    if not record:
        return ""
    access_token = str(record.get("access_token") or "").strip()
    expires_at = float(record.get("expires_at") or 0)
    if access_token and time.time() < expires_at - TOKEN_REFRESH_SKEW_SECONDS:
        return access_token
    refresh_token = str(record.get("refresh_token") or "").strip()
    if not refresh_token:
        _delete_record(settings, uid)
        return ""
    refreshed = _refresh_user_token(settings, refresh_token)
    if not refreshed:
        _delete_record(settings, uid)
        return ""
    expected = str(record.get("primary_user_id") or uid)
    save_user_token(settings, refreshed, expected_user_id=expected)
    return str(refreshed.get("access_token") or refreshed.get("user_access_token") or "").strip()


def _refresh_user_token(settings: Settings, refresh_token: str) -> dict[str, Any]:
    response = httpx.post(
        f"{_api_base(settings)}/open-apis/authen/v1/refresh_access_token",
        headers={"Content-Type": "application/json; charset=utf-8"},
        json={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30.0,
    )
    try:
        data = response.json()
    except Exception:
        data = {}
    if response.status_code >= 400 or data.get("code", 0) != 0:
        log.warning("refresh user_access_token failed: status=%s body=%s", response.status_code, data)
        return {}
    payload = data.get("data") or {}
    if isinstance(payload, dict) and not payload.get("refresh_token"):
        payload["refresh_token"] = refresh_token
    return payload if isinstance(payload, dict) else {}


def _record_from_token_payload(token_payload: dict[str, Any], expected_user_id: str = "") -> dict[str, Any]:
    access_token = str(token_payload.get("access_token") or token_payload.get("user_access_token") or "").strip()
    refresh_token = str(token_payload.get("refresh_token") or "").strip()
    expires_in = _as_int(token_payload.get("expires_in") or token_payload.get("expire"), 0)
    if not access_token:
        return {"primary_user_id": "", "aliases": []}
    aliases = _dedupe(
        [
            str(expected_user_id or "").strip(),
            str(token_payload.get("open_id") or "").strip(),
            str(token_payload.get("user_id") or "").strip(),
            str(token_payload.get("union_id") or "").strip(),
        ]
    )
    primary = aliases[0] if aliases else ""
    return {
        "primary_user_id": primary,
        "aliases": aliases,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(time.time()) + max(0, expires_in),
        "updated_at": int(time.time()),
    }


def _record_for_user(store: dict[str, Any], user_id: str) -> dict[str, Any]:
    users = store.get("users") if isinstance(store.get("users"), dict) else {}
    record = users.get(user_id)
    return record if isinstance(record, dict) else {}


def _delete_record(settings: Settings, user_id: str) -> None:
    store = _read_store(settings)
    users = store.get("users") if isinstance(store.get("users"), dict) else {}
    record = users.get(user_id)
    aliases = record.get("aliases") if isinstance(record, dict) else [user_id]
    for alias in aliases if isinstance(aliases, list) else [user_id]:
        users.pop(str(alias), None)
    _write_store(settings, store)


def _read_store(settings: Settings) -> dict[str, Any]:
    path = _store_path(settings)
    if not path.is_file():
        return {"users": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("failed to read oauth token store: %s", path)
        return {"users": {}}
    return data if isinstance(data, dict) else {"users": {}}


def _write_store(settings: Settings, store: dict[str, Any]) -> None:
    path = _store_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _store_path(settings: Settings) -> Path:
    raw = str(settings.oauth_token_store_path or "").strip()
    path = Path(raw or ".artifacts/auth/user_tokens.json")
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parents[3] / path


def _oauth_scope(settings: Settings) -> str:
    raw = str(settings.oauth_user_scopes or "").strip()
    if raw:
        return raw
    return " ".join(DEFAULT_USER_SCOPES)


def _serializer(settings: Settings) -> URLSafeSerializer:
    return URLSafeSerializer(settings.session_secret, salt="lark-oauth-state")


def _api_base(settings: Settings) -> str:
    return (settings.lark_base_url or "https://open.feishu.cn").rstrip("/")


def _urlencode(params: dict[str, str]) -> str:
    from urllib.parse import urlencode

    return urlencode(params)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


__all__ = [
    "build_feishu_authorize_url",
    "build_oauth_login_url",
    "decode_oauth_state",
    "encode_oauth_state",
    "get_valid_user_access_token",
    "save_user_token",
]
