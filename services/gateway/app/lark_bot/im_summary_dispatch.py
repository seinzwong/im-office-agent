from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Optional

from ..pipelines.summary_from_event import run_summary_for_chat

_pool: ThreadPoolExecutor | None = None


def im_summary_executor() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lark-im-summary")
    return _pool


def shutdown_im_summary_executor() -> None:
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=False)
        _pool = None


@dataclass(frozen=True)
class ImSummaryParams:
    chat_id: str
    time_hint: str
    t0_unix: int
    text: str
    message_id: str


def try_parse_im_event_for_summary(ev: dict[str, Any]) -> Optional[ImSummaryParams]:
    """从事件体（HTTP 回调里的 `event` 或与之一致的 dict）解析摘要任务参数。"""
    msg = ev.get("message", ev) or {}
    if not isinstance(msg, dict):
        return None

    chat_id = (msg.get("chat_id") or (ev.get("chat_id") or "unknown"))[:64]
    mid = (msg.get("message_id") or "unknown")[:100]
    content = msg.get("content", "")
    cj: dict[str, Any]
    if isinstance(content, str) and content.strip().startswith("{"):
        try:
            cj = json.loads(content)
        except Exception:  # noqa: BLE001
            cj = {"text": content}
    elif isinstance(content, str):
        cj = {"text": content}
    else:
        cj = content if isinstance(content, dict) else {"text": str(content)}
    text = str(cj.get("text", cj) or "")
    hint = text[:200] if not text.strip().startswith("@") else " ".join(text.split()[1:200])[:200]
    ct = msg.get("create_time")
    if isinstance(ct, int) and ct > 0:
        t0 = ct // 1000 if ct > 10_000_000_000 else ct
    else:
        t0 = int(time.time())
    if not chat_id or chat_id == "unknown":
        return None
    return ImSummaryParams(
        chat_id=chat_id,
        time_hint=hint,
        t0_unix=t0,
        text=text or "（无文本）",
        message_id=mid,
    )


def submit_im_summary_to_executor(p: ImSummaryParams) -> None:
    """长连接等场景：在线程池中执行同步的 `run_summary_for_chat`，避免阻塞 SDK 回调线程。"""
    im_summary_executor().submit(
        run_summary_for_chat,
        p.chat_id,
        p.time_hint,
        p.t0_unix,
        p.text,
        p.message_id,
    )
