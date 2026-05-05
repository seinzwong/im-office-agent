from __future__ import annotations

import json
import subprocess
from typing import Any
from xml.sax.saxutils import escape

from .ir_schema import ensure_ir_defaults, validate_ir
from .runtime import adapter_error, env_value, extract_folder_token, lark_cli_path, parse_json_object, run_lark_cli


def ir_to_ppt_draft(ir: dict) -> dict:
    ir = ensure_ir_defaults(ir)
    slides: list[dict] = []
    for block in ir.get("blocks", []):
        if not isinstance(block, dict):
            continue
        slides.append(_block_to_slide(block))
    if not slides:
        slides.append(
            {
                "slide_id": "cover",
                "layout": "cover",
                "title": ir["meta"]["title"],
                "content": {"subtitle": ir["meta"].get("subtitle", "")},
                "speaker_notes": "",
            }
        )
    return {"title": ir["meta"]["title"], "theme": ir.get("theme", {}), "slides": slides}


def publish_ir_to_ppt(ir: dict, options: dict) -> dict:
    warnings: list[str] = []
    normalized = ensure_ir_defaults(ir)
    errors = validate_ir(normalized)
    if errors:
        return adapter_error("IR_VALIDATION_FAILED", "IR validation failed", errors, warnings)
    draft = ir_to_ppt_draft(normalized)
    options = options or {}
    mode = str(options.get("mode") or "create")
    if mode not in {"create", "append"}:
        return adapter_error("INVALID_PUBLISH_MODE", "PPT mode must be create or append.", [mode], warnings)
    if options.get("dry_run", True):
        warnings.append("PPTX export is not connected in v1; returning PPTDraft slides JSON.")
        return {"ok": True, "mode": "dry_run", "ppt_draft": draft, "warnings": warnings}
    cli_path = lark_cli_path()
    if not cli_path:
        warnings.append("LARK_CLI_PATH is missing; returning PPTDraft slides JSON.")
        return {"ok": True, "mode": "dry_run", "ppt_draft": draft, "warnings": warnings}
    slides_xml = [_slide_to_xml(slide) for slide in draft["slides"][:10]]
    if mode == "append":
        return _append_slides_to_presentation(draft, slides_xml, options, warnings)
    command = [
        cli_path,
        "slides",
        "+create",
        "--title",
        draft["title"],
        "--slides",
        json.dumps(slides_xml, ensure_ascii=False),
        "--as",
        str(options.get("as") or "user"),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        return adapter_error("PPT_EXPORT_FAILED", str(exc), warnings=warnings)
    if completed.returncode != 0:
        return adapter_error(
            "PPT_EXPORT_FAILED",
            "lark-cli slides +create failed",
            {"stdout": completed.stdout, "stderr": completed.stderr, "returncode": completed.returncode},
            warnings,
        )
    cli_data = parse_json_object(completed.stdout)
    presentation_id = str((cli_data.get("data") or {}).get("xml_presentation_id") or "")
    move_result = None
    folder_token = extract_folder_token(options.get("folder_token") or env_value("FEISHU_DOC_FOLDER_TOKEN"))
    if presentation_id and folder_token:
        move_result = _move_slides_to_folder(cli_path, presentation_id, folder_token, str(options.get("as") or "user"))
        if move_result.get("ok") is not True:
            warnings.append(f"PPT created but move to folder failed: {move_result.get('error')}")
    return {
        "ok": True,
        "mode": "create",
        "presentation_id": presentation_id or None,
        "url": (cli_data.get("data") or {}).get("url"),
        "move_result": move_result,
        "ppt_draft": draft,
        "slides_xml_preview": slides_xml,
        "cli_stdout": completed.stdout,
        "warnings": warnings,
    }


def _append_slides_to_presentation(draft: dict, slides_xml: list[str], options: dict, warnings: list[str]) -> dict:
    presentation_id = str(options.get("presentation_id") or options.get("document_id") or "").strip()
    if not presentation_id:
        return adapter_error("INVALID_PUBLISH_MODE", "PPT append mode requires presentation_id.", [], warnings)
    created = []
    revision_id = int(options.get("revision_id") or -1)
    for slide_xml in slides_xml:
        result = run_lark_cli(
            [
                "slides",
                "xml_presentation.slide",
                "create",
                "--params",
                json.dumps({"xml_presentation_id": presentation_id, "revision_id": revision_id}, ensure_ascii=False),
                "--data",
                json.dumps({"slide": {"content": slide_xml}}, ensure_ascii=False),
                "--as",
                str(options.get("as") or "user"),
                "--yes",
            ]
        )
        if not result["ok"]:
            return adapter_error("PPT_EXPORT_FAILED", "lark-cli slides append failed.", result, warnings)
        data = result["json"].get("data") or result["json"]
        revision_id = int(data.get("revision_id") or revision_id)
        created.append(data)
    return {"ok": True, "mode": "append", "presentation_id": presentation_id, "slides_created": created, "ppt_draft": draft, "warnings": warnings}


def _block_to_slide(block: dict) -> dict:
    kind = block.get("kind")
    layout = {
        "cover": "cover",
        "split": "title_content",
        "flow": "flow",
        "metrics": "cards",
        "cards": "cards",
        "table": "table",
        "timeline": "timeline",
        "image": "title_content",
    }.get(str(kind), "title_content")
    content: dict[str, Any]
    if kind == "cover":
        content = {"subtitle": block.get("subtitle", ""), "kicker": block.get("kicker", "")}
    elif kind == "split":
        content = {"points": block.get("points", [])}
    elif kind == "flow":
        content = {"caption": block.get("caption", ""), "nodes": block.get("nodes", []), "edges": block.get("edges", [])}
    elif kind == "metrics":
        content = {"items": block.get("items", [])}
    elif kind == "cards":
        content = {"cards": block.get("cards", [])}
    elif kind == "table":
        content = {"columns": block.get("columns", []), "rows": block.get("rows", [])}
    elif kind == "timeline":
        content = {"events": block.get("events", [])}
    elif kind == "image":
        content = {"image": block.get("image"), "caption": block.get("caption", "")}
    else:
        content = {}
    return {
        "slide_id": str(block.get("id") or "slide"),
        "layout": layout,
        "title": str(block.get("title") or ""),
        "content": content,
        "speaker_notes": "",
    }


def _slide_to_xml(slide: dict) -> str:
    title = escape(str(slide.get("title") or ""))
    body_lines = _slide_body_lines(slide)
    body_xml = _paragraphs_xml(body_lines)
    return (
        '<slide xmlns="http://www.larkoffice.com/sml/2.0">'
        '<style><fill><fillColor color="rgb(248,250,252)"/></fill></style>'
        "<data>"
        '<shape type="text" topLeftX="70" topLeftY="55" width="820" height="90">'
        f'<content textType="title"><p>{title}</p></content>'
        "</shape>"
        '<shape type="text" topLeftX="90" topLeftY="170" width="780" height="300">'
        f'<content textType="body">{body_xml}</content>'
        "</shape>"
        "</data>"
        "</slide>"
    )


def _slide_body_lines(slide: dict) -> list[str]:
    content = slide.get("content") if isinstance(slide.get("content"), dict) else {}
    layout = slide.get("layout")
    if layout == "cover":
        return [str(content.get("kicker") or ""), str(content.get("subtitle") or "")]
    if "points" in content:
        return [f"- {point}" for point in content.get("points") or []]
    if "cards" in content:
        return [f"{card.get('title', '')}: {card.get('body', '')}" for card in content.get("cards") or [] if isinstance(card, dict)]
    if "items" in content:
        return [f"{item.get('label', '')}: {item.get('value', '')} {item.get('note', '')}" for item in content.get("items") or [] if isinstance(item, dict)]
    if "events" in content:
        return [f"{event.get('date', '')} {event.get('title', '')}: {event.get('body', '')}" for event in content.get("events") or [] if isinstance(event, dict)]
    if "nodes" in content:
        labels = [str(node.get("label") or node.get("id")) for node in content.get("nodes") or [] if isinstance(node, dict)]
        return [" -> ".join(labels)]
    if "columns" in content:
        return [" | ".join([str(cell) for cell in content.get("columns") or []])]
    return []

def _paragraphs_xml(lines: list[str]) -> str:
    clean = [line for line in lines if str(line).strip()]
    if not clean:
        return "<p></p>"
    return "".join(f"<p>{escape(str(line))}</p>" for line in clean)

def _move_slides_to_folder(cli_path: str, presentation_id: str, folder_token: str, identity: str) -> dict:
    command = [
        cli_path,
        "drive",
        "+move",
        "--file-token",
        presentation_id,
        "--type",
        "slides",
        "--folder-token",
        folder_token,
        "--as",
        identity,
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if completed.returncode != 0:
        return {
            "ok": False,
            "error": {"stdout": completed.stdout, "stderr": completed.stderr, "returncode": completed.returncode},
        }
    return {"ok": True, "stdout": completed.stdout}
