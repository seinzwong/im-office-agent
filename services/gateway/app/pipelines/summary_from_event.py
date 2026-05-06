from __future__ import annotations

import logging
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

from services.agent.agents import generate_content_ir_from_messages
from services.gateway.adapter import publish_ir

from ..agents.client import AgentsClient
from ..config import get_settings
from ..content_ir_store import save_content_ir_for_file_token
from ..feishu_openapi import (
    docx_create_in_folder_with_plain_text,
    docx_open_url,
    list_chat_messages,
    reply_text_to_message,
)
from ..oauth_tokens import build_oauth_login_url, get_valid_user_access_token

log = logging.getLogger(__name__)
CN_TZ = timezone(timedelta(hours=8))


def run_summary_command(
    chat_id: str,
    source_message_id: str,
    trigger_user_id: str,
    end_unix: int,
    minutes: int = 60,
    limit: int = 200,
) -> Optional[dict[str, Any]]:
    """Fetch scoped chat history, ask Agent for IR, publish doc, then reply."""
    s = get_settings()
    start_unix = int(end_unix) - max(1, int(minutes or 60)) * 60
    limit = max(1, min(int(limit or 200), 200))
    user_access_token = get_valid_user_access_token(s, trigger_user_id)
    if not user_access_token:
        user_access_token = (s.feishu_user_access_token or "").strip()
    if not user_access_token:
        _safe_reply(
            s,
            source_message_id,
            _auth_required_text(trigger_user_id, build_oauth_login_url(s, trigger_user_id, "summary")),
        )
        return {"ok": False, "error": {"code": "USER_AUTH_REQUIRED", "message": "User OAuth required."}}
    try:
        messages = list_chat_messages(s, chat_id, start_unix, end_unix, limit)
    except Exception as exc:  # noqa: BLE001
        log.exception("list chat messages failed: %s", exc)
        _safe_reply(
            s,
            source_message_id,
            _summary_failed_text(trigger_user_id, "暂时拉取不到群消息，请稍后再试。"),
        )
        return {"ok": False, "error": {"code": "MESSAGE_LIST_FAILED", "message": str(exc)}}

    try:
        agent_result = _invoke_summary_ir(chat_id, messages, start_unix, end_unix, source_message_id)
        if not isinstance(agent_result, dict) or agent_result.get("ok") is not True:
            raise RuntimeError(_agent_error_message(agent_result))
        ir = (agent_result.get("result") or {}).get("ir") if isinstance(agent_result, dict) else None
        if not isinstance(ir, dict):
            raise RuntimeError("Agent did not return result.ir")
    except Exception as exc:  # noqa: BLE001
        log.exception("summary IR generation failed: %s", exc)
        _safe_reply(
            s,
            source_message_id,
            _summary_failed_text(trigger_user_id, "总结生成服务暂时不可用，请稍后再试。"),
        )
        return {"ok": False, "error": {"code": "SUMMARY_IR_FAILED", "message": str(exc)}}

    try:
        publish_result = publish_ir(
            ir,
            {
                "folder_token": s.artifacts_drive_folder_token,
                "user_access_token": user_access_token,
            },
            {"target_outputs": ["doc"], "dry_run": bool(s.dev_skip_lark)},
        )
        doc_result = (publish_result.get("publish_result") or {}).get("doc") or {}
        if publish_result.get("ok") is not True or doc_result.get("ok") is not True:
            error = publish_result.get("error") or doc_result.get("error") or publish_result
            if _requires_user_reauthorization(error):
                _safe_reply(
                    s,
                    source_message_id,
                    _auth_required_text(trigger_user_id, build_oauth_login_url(s, trigger_user_id, "summary")),
                )
                return {
                    "ok": False,
                    "error": {
                        "code": "USER_AUTH_REQUIRED",
                        "message": "User OAuth token lacks required Feishu document scopes.",
                    },
                }
            message = str(error)
            if "no folder permission" in message or "1770040" in message:
                message = (
                    "当前文档目录需要用户授权才能创建文档。请配置 FEISHU_USER_ACCESS_TOKEN/"
                    "feishu_user_access_token，或把目标文件夹授权给应用。"
                )
            raise RuntimeError(message)
        document_id = str(doc_result.get("document_id") or "")
        open_url = str(doc_result.get("url") or docx_open_url(s, document_id) or "")
    except Exception as exc:  # noqa: BLE001
        log.exception("publish IR doc failed: %s", exc)
        if _requires_user_reauthorization(exc):
            _safe_reply(
                s,
                source_message_id,
                _auth_required_text(trigger_user_id, build_oauth_login_url(s, trigger_user_id, "summary")),
            )
            return {
                "ok": False,
                "error": {
                    "code": "USER_AUTH_REQUIRED",
                    "message": "User OAuth token lacks required Feishu document scopes.",
                },
            }
        _safe_reply(
            s,
            source_message_id,
            _summary_failed_text(trigger_user_id, "文档创建失败，请检查飞书文档权限后重试。"),
        )
        return {"ok": False, "error": {"code": "PUBLISH_DOC_FAILED", "message": str(exc)}}

    reply_text = _summary_done_text(
        trigger_user_id,
        start_unix,
        end_unix,
        len(messages),
        minutes,
        limit,
        open_url,
    )
    _safe_reply(s, source_message_id, reply_text)
    _save_summary_content_ir(document_id, open_url, messages, chat_id, minutes)
    return {
        "ok": True,
        "chat_id": chat_id,
        "source_message_id": source_message_id,
        "message_count": len(messages),
        "time_window_start_unix": start_unix,
        "time_window_end_unix": end_unix,
        "open_url": open_url,
    }


def _save_summary_content_ir(document_id: str, open_url: str, messages: list[dict], chat_id: str, minutes: int | None) -> None:
    if not document_id:
        return
    request = {
        "task": {
            "task_id": f"summary_{chat_id}",
            "title": f"Chat summary {chat_id}",
            "goal": f"Summarize the selected chat window ({minutes or 60} minutes).",
            "audience": "Management and business team",
            "deliverables": ["doc"],
        },
        "messages": messages,
        "options": {"target_outputs": ["doc"], "dry_run": True, "language": "zh-CN"},
    }
    try:
        result = generate_content_ir_from_messages(request)
        content_ir = result.get("content_ir") if result.get("ok") is True else None
        if isinstance(content_ir, dict):
            save_content_ir_for_file_token(document_id, content_ir, source_title=request["task"]["title"], source_url=open_url)
    except Exception as exc:  # noqa: BLE001
        log.warning("content_ir sidecar save failed: %s", exc)


def _invoke_summary_ir(
    chat_id: str,
    messages: list[dict[str, str]],
    start_unix: int,
    end_unix: int,
    source_message_id: str,
) -> dict[str, Any]:
    ag = AgentsClient()
    return ag.invoke(
        "summary_from_chat",
        {
            "chat_id": chat_id,
            "time_window": {
                "start_unix": start_unix,
                "end_unix": end_unix,
                "label": f"{_format_time(start_unix)} 至 {_format_time(end_unix)}",
            },
            "task": {
                "task_id": f"summary_{source_message_id or int(end_unix)}",
                "title": "群聊目标与方案总结",
                "goal": "整理群聊中的主要目标以及产出的方案，提炼共识、问题、行动路径、风险与下一步。",
                "audience": "群聊成员",
                "deliverables": ["doc"],
            },
            "messages": messages,
            "options": {
                "language": "zh-CN",
                "target_outputs": ["doc"],
                "dry_run": False,
            },
        },
        trace_id=f"summary-{source_message_id}",
    )


def _agent_error_message(agent_result: Any) -> str:
    if not isinstance(agent_result, dict):
        return f"Agent returned invalid response: {agent_result!r}"
    error = agent_result.get("error")
    if not isinstance(error, dict):
        return f"Agent failed: {agent_result!r}"
    code = str(error.get("code") or "AGENT_FAILED")
    message = str(error.get("message") or "")
    details = error.get("details")
    suffix = f" details={details!r}" if details is not None else ""
    return f"{code}: {message}{suffix}"


def _requires_user_reauthorization(error: Any) -> bool:
    text = str(error)
    return (
        "99991679" in text
        and (
            "docx:document" in text
            or "docx:document:readonly" in text
            or "docx:document:create" in text
            or "docx:document:write_only" in text
        )
    )


def _safe_reply(settings, message_id: str, text: str) -> None:
    try:
        reply_text_to_message(settings, message_id, text)
    except Exception as exc:  # noqa: BLE001
        log.warning("lark summary reply failed: %s", exc)


def _summary_done_text(
    user_id: str,
    start_unix: int,
    end_unix: int,
    message_count: int,
    minutes: int,
    limit: int,
    open_url: str,
) -> str:
    prefix = f'<at user_id="{user_id}"></at>\n' if user_id else ""
    link = f"\n文档已生成：{open_url}" if open_url else ""
    return (
        f"{prefix}"
        f"已收到 /summary。本次纳入统计的聊天时间：自 {_format_time(start_unix)} 至 {_format_time(end_unix)}，"
        f"共 {message_count} 条消息。（时间窗：最近 {minutes} 分钟；单次最多拉取 {limit} 条。）"
        f"{link}"
    )


def _summary_failed_text(user_id: str, message: str) -> str:
    prefix = f'<at user_id="{user_id}"></at>\n' if user_id else ""
    return f"{prefix}{message}"


def _auth_required_text(user_id: str, auth_url: str) -> str:
    prefix = f'<at user_id="{user_id}"></at>\n' if user_id else ""
    return (
        f"{prefix}"
        "需要你授权飞书文档权限后，我才能在你有权限的云盘目录里创建总结文档。\n"
        f"请打开授权链接完成授权，然后重新发送 /summary：{auth_url}"
    )


def _format_time(unix_seconds: int) -> str:
    return datetime.fromtimestamp(int(unix_seconds), CN_TZ).strftime("%Y-%m-%d %H:%M")


def run_summary_for_chat(
    chat_id: str,
    time_hint: str,
    t0_unix: int,
    aggregate_text: str,
    source_message_id: str = "",
) -> Optional[dict[str, Any]]:
    """用 Agents 出文档结构 + lark docs 落盘到配置目录；不落库。返回 {title, open_url, file_token, chat_id, time_hint, ok, ...}。"""
    s = get_settings()
    ag = AgentsClient()
    try:
        res = ag.invoke(
            "summary_from_chat",
            {
                "chat_id": chat_id,
                "message_text_aggregated": aggregate_text,
                "time_hint_text": time_hint,
                "default_window_end_unix": t0_unix,
            },
            trace_id=f"summary-{source_message_id}",
        )
    except Exception as e:  # noqa: BLE001
        log.exception("agents invoke: %s", e)
        res = {
            "ok": False,
            "error": {"code": "AGENTS_DOWN", "message": str(e)},
        }
    if not res.get("ok", True):
        title = f"摘要失败 {chat_id[:8]}"
        return {
            "ok": False,
            "title": title,
            "open_url": "about:blank#failed",
            "file_token": "",
            "chat_id": chat_id,
            "time_hint": time_hint,
            "source_message_id": source_message_id,
        }
    r = res.get("result", {}) or {}
    title = r.get("doc_title") or f"群聊总结 {chat_id[:8]}"
    xml = r.get("doc_content_xml") or f"<title>{title}</title><p>（无内容）</p>"
    open_url, file_token = _write_doc_or_dev(s, title, xml)
    tw: Tuple[Optional[int], Optional[int]] = (t0_unix - 86400, t0_unix)
    if (res.get("result") or {}).get("time_window"):
        tww = (res.get("result") or {}).get("time_window", {}) or {}
        st, en = tww.get("start_unix"), tww.get("end_unix")
        if st is not None and en is not None:
            tw = (st, en)
    return {
        "ok": True,
        "title": title,
        "open_url": open_url,
        "file_token": file_token,
        "chat_id": chat_id,
        "time_hint": time_hint,
        "source_message_id": source_message_id,
        "time_window_start_unix": tw[0],
        "time_window_end_unix": tw[1],
    }


def _write_doc_or_dev(settings, title: str, content_xml: str) -> tuple[str, str]:
    if settings.dev_skip_lark or not (settings.artifacts_drive_folder_token or "").strip():
        log.info("dev_skip: skip lark write for doc %s", title)
        q = urllib.parse.urlencode({"title": title[:40]})
        return (f"https://dev-placeholder.invalid/docx?{q}", "dev")
    folder = (settings.artifacts_drive_folder_token or "").strip()
    safe_xml = content_xml
    if "<" in safe_xml and len(safe_xml) > 500_000:
        safe_xml = safe_xml[:500_000] + "<p>（内容已截断）</p>"
    try:
        u, t = docx_create_in_folder_with_plain_text(settings, folder, title, safe_xml)
        if t:
            return (u, t)
        return (u or f"https://open.feishu.cn?title={urllib.parse.quote(title[:20])}", "")
    except Exception as e:  # noqa: BLE001
        log.warning("docx OpenAPI create: %s", e)
    return (f"https://open.feishu.cn?title={urllib.parse.quote(title[:20])}", "")
