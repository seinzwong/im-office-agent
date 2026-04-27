from __future__ import annotations

import logging
import uuid

from ..agents import AgentsClient
from ..config import get_settings
from ..drive_artifacts import get_artifact_body_for_file_token

log = logging.getLogger(__name__)


def run_deliver_artifacts(
    file_tokens: list[str],
    want_whiteboard: bool,
    want_slides: bool,
) -> None:
    """不持久化任务：后台调 Agents（及可选 lark 占位调用）。以 file_token 在目录列表中的元数据构造正文。"""
    if not file_tokens or (not want_whiteboard and not want_slides):
        return
    s = get_settings()
    ag = AgentsClient()
    task_ref = str(uuid.uuid4())[:8]
    for ftk in file_tokens:
        title, open_url, body = get_artifact_body_for_file_token(ftk)
        if not body:
            body = f"{title}\n{open_url}" if (title or open_url) else f"file_token={ftk}"
        if want_whiteboard:
            r = ag.invoke(
                "deliver_whiteboard",
                {"document_id": ftk, "body_plain": body},
                context={"task_ref": task_ref, "file_token": ftk},
            )
            if not r.get("ok"):
                log.warning(
                    "deliver_whiteboard: %s",
                    (r.get("error") or {}).get("message", r),
                )
        if want_slides:
            r2 = ag.invoke(
                "deliver_slides",
                {"document_id": ftk, "body_plain": body},
                context={"task_ref": task_ref, "file_token": ftk},
            )
            if not r2.get("ok"):
                log.warning(
                    "deliver_slides: %s",
                    (r2.get("error") or {}).get("message", r2),
                )
