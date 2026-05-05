from __future__ import annotations

import logging
import threading
from typing import Any

from ..config import Settings

log = logging.getLogger(__name__)

_connect_patch_installed = False


def _domain_for_sdk(s: Settings) -> str:
    base = (s.lark_base_url or "").strip().rstrip("/")
    if not base:
        return "https://open.feishu.cn"
    return base


def _ws_thread_main(s: Settings) -> None:
    """
    在独立线程内首次 import lark_oapi，使 SDK 内 asyncio 循环绑定到本线程，
    避免与 uvicorn 主事件循环冲突。
    """
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    import lark_oapi as lark
    from lark_oapi.ws.client import Client

    from .im_at_bot import (
        im_receive_should_reply_not_ready_sdk,
        submit_not_ready_reply_chat,
    )
    from .im_summary_dispatch import submit_im_summary_to_executor, try_parse_im_event_for_summary
    from .join_welcome import submit_user_added_welcome

    global _connect_patch_installed
    if not _connect_patch_installed:
        _orig_client_connect = Client._connect

        async def _connect_with_log(self: Any) -> None:
            await _orig_client_connect(self)
            url = (getattr(self, "_conn_url", None) or "")[:160]
            log.info("飞书长连接：WebSocket 已建立 url=%s", url or "(empty)")

        Client._connect = _connect_with_log  # type: ignore[method-assign]
        _connect_patch_installed = True

    ek = (s.lark_event_encrypt_key or "").strip()
    vt = (s.lark_verification_token or "").strip()

    def on_p2_im_message_receive_v1(data: Any) -> None:
        try:
            ev_obj = getattr(data, "event", None)
            m = getattr(ev_obj, "message", None) if ev_obj else None
            if not m:
                return
            not_ready, cid = im_receive_should_reply_not_ready_sdk(m)
            if not_ready:
                submit_not_ready_reply_chat(cid)
                return
            ev: dict[str, Any] = {}
            if ev_obj and m:
                ev["message"] = {
                    "chat_id": getattr(m, "chat_id", None) or "",
                    "message_id": getattr(m, "message_id", None) or "",
                    "content": getattr(m, "content", None) or "",
                    "create_time": getattr(m, "create_time", None),
                }
            params = try_parse_im_event_for_summary(ev)
            if params:
                submit_im_summary_to_executor(params)
        except Exception:  # noqa: BLE001
            log.exception("WS p2.im.message.receive_v1")

    def _noop_p2_im(_data: Any) -> None:
        """控制台若订阅了成员/机器人变更等事件，须注册处理器，否则 SDK 报 processor not found。"""

    event_handler = (
        lark.EventDispatcherHandler.builder(ek, vt, lark.LogLevel.INFO)
        .register_p2_im_message_receive_v1(on_p2_im_message_receive_v1)
        .register_p2_im_chat_member_bot_added_v1(_noop_p2_im)
        .register_p2_im_chat_member_bot_deleted_v1(_noop_p2_im)
        .register_p2_im_chat_member_user_added_v1(
            lambda d: submit_user_added_welcome(d),
        )
        .build()
    )

    cli = Client(
        s.lark_app_id.strip(),
        s.lark_app_secret.strip(),
        log_level=lark.LogLevel.INFO,
        event_handler=event_handler,
        domain=_domain_for_sdk(s),
    )
    log.info("飞书长连接：工作线程已启动，正在请求接入点并握手…")
    try:
        cli.start()
    except Exception as e:  # noqa: BLE001
        log.exception("飞书长连接：Client.start 异常退出 err=%s", e)
        raise


def start_lark_ws_client_background(s: Settings) -> threading.Thread:
    t = threading.Thread(target=_ws_thread_main, args=(s,), name="lark-ws", daemon=True)
    t.start()
    return t
