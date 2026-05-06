from __future__ import annotations

from datetime import datetime, timezone
import logging
import uuid

from services.gateway.planb_e2e import run_planb_e2e

from ..config import get_settings
from ..content_ir_store import load_content_ir_for_file_token, save_content_ir_for_file_token
from ..drive_artifacts import get_artifact_body_for_file_token

log = logging.getLogger(__name__)


def run_deliver_artifacts(
    file_tokens: list[str],
    want_whiteboard: bool,
    want_slides: bool,
) -> None:
    """Generate requested deliverables through the PlanB IR publisher."""
    log.info(
        "planb deliver started file_count=%s whiteboard=%s slides=%s",
        len(file_tokens),
        want_whiteboard,
        want_slides,
    )
    if not file_tokens or (not want_whiteboard and not want_slides):
        log.info("planb deliver skipped empty input or no targets")
        return

    settings = get_settings()
    task_ref = str(uuid.uuid4())[:8]
    targets: list[str] = []
    if want_whiteboard:
        targets.append("board")
    if want_slides:
        targets.append("ppt")

    messages: list[dict[str, str]] = []
    source_titles: list[str] = []
    source_records: list[tuple[str, str, str]] = []
    for index, file_token in enumerate(file_tokens):
        title, open_url, body = get_artifact_body_for_file_token(file_token)
        if not body:
            body = f"{title}\n{open_url}".strip() if (title or open_url) else f"file_token={file_token}"
        source_titles.append(title or file_token)
        source_records.append((file_token, title, open_url))
        messages.append(
            {
                "message_id": f"artifact_{index + 1}_{file_token}",
                "sender": "selected_artifact",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "text": "\n".join(
                    part
                    for part in (
                        f"Source document: {title}" if title else "",
                        f"Open URL: {open_url}" if open_url else "",
                        body,
                    )
                    if part
                ),
            }
        )

    options: dict = {
        "target_outputs": targets,
        "dry_run": False,
        "language": "zh-CN",
        "board": {"as": "user"},
        "ppt": {"as": "user"},
    }
    ppt_content_ir = _stored_content_ir_for_ppt(file_tokens, want_slides)
    if ppt_content_ir is not None:
        options["content_ir"] = ppt_content_ir

    request = {
        "task": {
            "task_id": f"deliver_{task_ref}",
            "title": _deliver_title(source_titles, targets),
            "goal": "Generate deliverables from selected source documents.",
            "audience": "Management and business team",
            "deliverables": targets,
        },
        "messages": messages,
        "publish_context": {
            "folder_token": (settings.artifacts_drive_folder_token or "").strip() or None,
        },
        "options": options,
    }

    result = run_planb_e2e(request)
    if result.get("ok") is not True:
        log.warning("planb deliver failed: %s", result.get("error") or result)
        return
    _save_generated_content_ir(source_records, result.get("content_ir"), want_slides)
    log.info(
        "planb deliver finished targets=%s warnings=%s result=%s",
        targets,
        result.get("warnings") or [],
        result.get("publish_result"),
    )


def _deliver_title(source_titles: list[str], targets: list[str]) -> str:
    target_label = " / ".join({"board": "Board", "ppt": "PPT"}.get(target, target) for target in targets)
    clean_titles = [title for title in source_titles if title]
    if not clean_titles:
        return f"Generated {target_label}"
    first = clean_titles[0]
    suffix = "" if len(clean_titles) == 1 else f" and {len(clean_titles) - 1} more"
    return f"{first}{suffix} - {target_label}"


def _stored_content_ir_for_ppt(file_tokens: list[str], want_slides: bool) -> dict | None:
    if not want_slides or len(file_tokens) != 1:
        return None
    return load_content_ir_for_file_token(file_tokens[0])


def _save_generated_content_ir(source_records: list[tuple[str, str, str]], content_ir: object, want_slides: bool) -> None:
    if not want_slides or not isinstance(content_ir, dict):
        return
    for file_token, title, open_url in source_records:
        save_content_ir_for_file_token(file_token, content_ir, source_title=title, source_url=open_url)
