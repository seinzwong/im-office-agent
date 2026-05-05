from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

JsonDict = dict[str, Any]


def normalize_agent_ir_request(raw: dict) -> dict:
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return _error("INVALID_REQUEST", "Agent IR request must be an object.", [], warnings)

    task = _normalize_task(raw, warnings)
    messages = _normalize_messages(raw, warnings)
    if not messages:
        return _error("NO_SCOPE_MESSAGES", "No scope messages found in request.", [], warnings)

    publish_context = _normalize_publish_context(raw, warnings)
    options = _normalize_options(raw, task, warnings)

    return {
        "ok": True,
        "request": {
            "task": task,
            "messages": messages,
            "assets": _as_list(raw.get("assets")),
            "publish_context": publish_context,
            "options": options,
        },
        "warnings": _dedupe(warnings),
    }


def _normalize_task(raw: JsonDict, warnings: list[str]) -> JsonDict:
    source = _as_dict(raw.get("task"))
    task_id = _first_non_empty(
        source.get("task_id"),
        source.get("id"),
        raw.get("task_id"),
        raw.get("taskId"),
        "task",
    )
    if source.get("id") and not source.get("task_id"):
        warnings.append("Mapped task.id to task.task_id.")
    if raw.get("taskId") and not source.get("task_id"):
        warnings.append("Mapped taskId to task.task_id.")

    title = _first_non_empty(source.get("title"), source.get("name"), raw.get("title"))
    if source.get("name") and not source.get("title"):
        warnings.append("Mapped task.name to task.title.")

    deliverables = _string_list(
        source.get("deliverables"),
        raw.get("deliverables"),
        raw.get("target_outputs"),
    ) or ["doc"]

    return {
        "task_id": str(task_id),
        "title": str(title) if title is not None else None,
        "goal": _nullable_str(_first_non_empty(source.get("goal"), raw.get("goal"))),
        "audience": _nullable_str(_first_non_empty(source.get("audience"), raw.get("audience"))),
        "deliverables": deliverables,
        "deadline": _nullable_str(_first_non_empty(source.get("deadline"), raw.get("deadline"))),
    }


def _normalize_messages(raw: JsonDict, warnings: list[str]) -> list[JsonDict]:
    source = _first_list(
        raw.get("messages"),
        raw.get("scope_messages"),
        raw.get("selected_messages"),
        raw.get("chat_messages"),
    )
    if source is raw.get("scope_messages"):
        warnings.append("Mapped scope_messages to messages.")
    elif source is raw.get("selected_messages"):
        warnings.append("Mapped selected_messages to messages.")
    elif source is raw.get("chat_messages"):
        warnings.append("Mapped chat_messages to messages.")

    messages: list[JsonDict] = []
    for index, item in enumerate(source):
        if not isinstance(item, dict):
            warnings.append(f"Skipped messages[{index}] because it is not an object.")
            continue
        text = _first_non_empty(item.get("text"), item.get("content"), item.get("plain_text"))
        if item.get("content") and not item.get("text"):
            warnings.append(f"Mapped messages[{index}].content to text.")
        if item.get("plain_text") and not item.get("text"):
            warnings.append(f"Mapped messages[{index}].plain_text to text.")
        text = str(text or "").strip()
        if not text:
            warnings.append(f"Skipped messages[{index}] because text is missing.")
            continue
        sender = _first_non_empty(item.get("sender"), item.get("sender_name"), item.get("user_name"), "unknown")
        if item.get("sender_name") and not item.get("sender"):
            warnings.append(f"Mapped messages[{index}].sender_name to sender.")
        if item.get("user_name") and not item.get("sender"):
            warnings.append(f"Mapped messages[{index}].user_name to sender.")
        timestamp = _first_non_empty(item.get("timestamp"), item.get("create_time"), item.get("time"))
        messages.append(
            {
                "message_id": str(_first_non_empty(item.get("message_id"), item.get("id"), f"msg_{index + 1}")),
                "sender": str(sender),
                "timestamp": str(timestamp or _now_iso()),
                "text": text,
            }
        )
    return messages


def _normalize_publish_context(raw: JsonDict, warnings: list[str]) -> JsonDict:
    source = _as_dict(raw.get("publish_context"))
    folder_token = _first_non_empty(
        source.get("folder_token"),
        raw.get("folder_token"),
        raw.get("folderToken"),
        raw.get("target_folder_token"),
    )
    if raw.get("folderToken") and not source.get("folder_token"):
        warnings.append("Mapped folderToken to publish_context.folder_token.")
    if raw.get("target_folder_token") and not source.get("folder_token"):
        warnings.append("Mapped target_folder_token to publish_context.folder_token.")

    authorized_user_id = _first_non_empty(
        source.get("authorized_user_id"),
        raw.get("authorized_user_id"),
        raw.get("user_id"),
        raw.get("open_id"),
    )
    if raw.get("user_id") and not source.get("authorized_user_id"):
        warnings.append("Mapped user_id to publish_context.authorized_user_id.")
    if raw.get("open_id") and not source.get("authorized_user_id"):
        warnings.append("Mapped open_id to publish_context.authorized_user_id.")

    return {
        "authorized_user_id": _nullable_str(authorized_user_id),
        "folder_token": _nullable_str(folder_token),
        "tenant_access_token": _nullable_str(source.get("tenant_access_token") or raw.get("tenant_access_token")),
        "user_access_token": _nullable_str(source.get("user_access_token") or raw.get("user_access_token")),
    }


def _normalize_options(raw: JsonDict, task: JsonDict, warnings: list[str]) -> JsonDict:
    source = _as_dict(raw.get("options"))
    targets = _string_list(
        source.get("target_outputs"),
        raw.get("target_outputs"),
        source.get("deliverables"),
        raw.get("deliverables"),
        task.get("deliverables"),
    ) or ["doc"]
    if raw.get("deliverables") and not raw.get("target_outputs"):
        warnings.append("Mapped deliverables to options.target_outputs.")
    if "all" in targets:
        targets = ["doc", "board", "ppt"]
    targets = [target for target in targets if target in {"doc", "board", "ppt"}] or ["doc"]

    return {
        "language": str(source.get("language") or raw.get("language") or "zh-CN"),
        "target_outputs": _dedupe(targets),
        "dry_run": _bool(source.get("dry_run"), default=_bool(raw.get("dry_run"), default=True)),
    }


def _error(code: str, message: str, details: Any | None = None, warnings: list[str] | None = None) -> JsonDict:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"ok": False, "error": error, "warnings": warnings or []}


def _as_dict(value: Any) -> JsonDict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first_list(*values: Any) -> list[Any]:
    for value in values:
        if isinstance(value, list):
            return value
    return []


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _string_list(*values: Any) -> list[str]:
    for value in values:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _nullable_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dedupe(items: list[Any]) -> list[Any]:
    output = []
    seen = set()
    for item in items:
        marker = repr(item)
        if marker not in seen:
            seen.add(marker)
            output.append(item)
    return output


__all__ = ["normalize_agent_ir_request"]
