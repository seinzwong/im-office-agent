from __future__ import annotations

from datetime import datetime, timezone
import logging
import uuid

from services.gateway.planb_e2e import run_planb_e2e

from ..config import get_settings
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
    for index, file_token in enumerate(file_tokens):
        title, open_url, body = get_artifact_body_for_file_token(file_token)
        if not body:
            body = f"{title}\n{open_url}".strip() if (title or open_url) else f"file_token={file_token}"
        source_titles.append(title or file_token)
        messages.append(
            {
                "message_id": f"artifact_{index + 1}_{file_token}",
                "sender": "selected_artifact",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "text": "\n".join(
                    part
                    for part in (
                        f"来源文档：{title}" if title else "",
                        f"打开链接：{open_url}" if open_url else "",
                        body,
                    )
                    if part
                ),
            }
        )

    request = {
        "task": {
            "task_id": f"deliver_{task_ref}",
            "title": _deliver_title(source_titles, targets),
            "goal": "基于选中的来源文档生成交付物",
            "audience": "管理层/业务团队",
            "deliverables": targets,
        },
        "messages": messages,
        "publish_context": {
            "folder_token": (settings.artifacts_drive_folder_token or "").strip() or None,
        },
        "options": {
            "target_outputs": targets,
            "dry_run": False,
            "language": "zh-CN",
            "board": {"as": "user"},
            "ppt": {"as": "user"},
        },
    }

    result = run_planb_e2e(request)
    if result.get("ok") is not True:
        log.warning("planb deliver failed: %s", result.get("error") or result)
        return
    log.info(
        "planb deliver finished targets=%s warnings=%s result=%s",
        targets,
        result.get("warnings") or [],
        result.get("publish_result"),
    )


def _deliver_title(source_titles: list[str], targets: list[str]) -> str:
    target_label = "、".join({"board": "画板", "ppt": "PPT"}.get(target, target) for target in targets)
    clean_titles = [title for title in source_titles if title]
    if not clean_titles:
        return f"生成{target_label}交付物"
    first = clean_titles[0]
    suffix = "" if len(clean_titles) == 1 else f"等 {len(clean_titles)} 个文档"
    return f"{first}{suffix} - {target_label}交付"
