from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .ir_schema import ensure_ir_defaults, validate_ir
from .runtime import adapter_error, env_value, extract_folder_token, lark_cli_path, parse_json_object, run_lark_cli


def slide_draft_to_ppt_draft(slide_draft: dict) -> dict:
    draft = slide_draft if isinstance(slide_draft, dict) else {}
    slides = []
    for index, slide in enumerate(draft.get("slides") or []):
        if not isinstance(slide, dict):
            continue
        content = slide.get("content") if isinstance(slide.get("content"), dict) else {}
        slides.append(
            {
                "slide_id": str(slide.get("id") or f"slide_{index + 1}"),
                "layout": str(slide.get("layout") or "summary_next_steps"),
                "title": str(slide.get("title") or ""),
                "content": content,
                "visual": slide.get("visual") if isinstance(slide.get("visual"), dict) else {},
                "asset_key": str(slide.get("asset_key") or slide.get("assetKey") or slide.get("image") or ""),
                "speaker_notes": str(slide.get("speaker_notes") or ""),
            }
        )
    return {
        "title": str(draft.get("title") or "Generated Presentation"),
        "subtitle": str(draft.get("subtitle") or ""),
        "theme": draft.get("theme") if isinstance(draft.get("theme"), dict) else {},
        "assets": draft.get("assets") if isinstance(draft.get("assets"), dict) else {},
        "slides": slides,
    }


def slide_draft_to_xml(slide_draft: dict) -> list[str]:
    draft = slide_draft_to_ppt_draft(slide_draft)
    return [_slide_to_xml(slide, draft.get("theme") or {}, index + 1, len(draft["slides"])) for index, slide in enumerate(draft["slides"])]


def publish_ir_to_ppt(ir: dict, options: dict) -> dict:
    warnings: list[str] = []
    normalized = ensure_ir_defaults(ir)
    errors = validate_ir(normalized)
    if errors:
        return adapter_error("IR_VALIDATION_FAILED", "IR validation failed", errors, warnings)
    slide_draft = options.get("slide_draft") if isinstance(options.get("slide_draft"), dict) else None
    if not slide_draft:
        return adapter_error(
            "SLIDE_DRAFT_REQUIRED",
            "PPT publishing requires SlideDraft; legacy IR-to-PPT fallback has been removed.",
            [],
            warnings,
        )
    draft = slide_draft_to_ppt_draft(slide_draft)
    options = options or {}
    mode = str(options.get("mode") or "create")
    if mode not in {"create", "append"}:
        return adapter_error("INVALID_PUBLISH_MODE", "PPT mode must be create or append.", [mode], warnings)
    renderer = str(options.get("renderer") or options.get("ppt_renderer") or "pptx").lower()
    if renderer != "xml":
        pptx_result = _publish_pptx_draft(draft, options, warnings)
        if pptx_result.get("ok") is True or options.get("fallback_to_xml") is False:
            return pptx_result
        warnings.extend(pptx_result.get("warnings") or [])
        warnings.append("PPTX renderer failed; falling back to Feishu Slides XML.")
    if options.get("dry_run", True):
        warnings.append("PPTX export is not connected in v1; returning PPTDraft slides JSON.")
        slides_xml = [_slide_to_xml(slide, draft.get("theme") or {}, index + 1, len(draft["slides"])) for index, slide in enumerate(draft["slides"][:10])]
        return {"ok": True, "mode": "dry_run", "ppt_draft": draft, "slides_xml_preview": slides_xml, "warnings": warnings}
    cli_path = lark_cli_path()
    if not cli_path:
        warnings.append("LARK_CLI_PATH is missing; returning PPTDraft slides JSON.")
        return {"ok": True, "mode": "dry_run", "ppt_draft": draft, "warnings": warnings}
    slides_xml = [_slide_to_xml(slide, draft.get("theme") or {}, index + 1, len(draft["slides"])) for index, slide in enumerate(draft["slides"][:10])]
    if mode == "append":
        return _append_slides_to_presentation(draft, slides_xml, options, warnings)
    identity = str(options.get("as") or "user")
    create_slides = slides_xml
    append_after_create = False
    slides_arg = json.dumps(create_slides, ensure_ascii=False, separators=(",", ":"))
    if len(slides_arg) > 20000 and len(slides_xml) > 1:
        warnings.append("Slides XML is large; creating first slide then appending the rest to avoid Windows command length limits.")
        create_slides = slides_xml[:1]
        append_after_create = True
        slides_arg = json.dumps(create_slides, ensure_ascii=False, separators=(",", ":"))
    command = [
        cli_path,
        "slides",
        "+create",
        "--title",
        draft["title"],
        "--slides",
        slides_arg,
        "--as",
        identity,
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
    cli_payload = cli_data.get("data") or {}
    presentation_id = str(cli_payload.get("xml_presentation_id") or "")
    append_result = None
    if append_after_create and presentation_id:
        append_options = {
            **options,
            "presentation_id": presentation_id,
            "revision_id": int(cli_payload.get("revision_id") or -1),
            "as": identity,
        }
        append_result = _append_slides_to_presentation(draft, slides_xml[1:], append_options, warnings)
        if append_result.get("ok") is not True:
            return adapter_error(
                "PPT_EXPORT_FAILED",
                "lark-cli slides append after create failed",
                append_result,
                warnings,
            )
    move_result = None
    folder_token = extract_folder_token(options.get("folder_token") or env_value("FEISHU_DOC_FOLDER_TOKEN"))
    if presentation_id and folder_token:
        move_result = _move_slides_to_folder(cli_path, presentation_id, folder_token, identity)
        if move_result.get("ok") is not True:
            warnings.append(f"PPT created but move to folder failed: {move_result.get('error')}")
    return {
        "ok": True,
        "mode": "create",
        "presentation_id": presentation_id or None,
        "url": (cli_data.get("data") or {}).get("url"),
        "append_result": append_result,
        "move_result": move_result,
        "ppt_draft": draft,
        "slides_xml_preview": slides_xml,
        "cli_stdout": completed.stdout,
        "warnings": warnings,
    }


def _publish_pptx_draft(draft: dict, options: dict, warnings: list[str]) -> dict:
    render_result = _render_pptx_with_node(draft, options)
    if render_result.get("ok") is not True:
        return adapter_error("PPTX_RENDER_FAILED", "pptxgenjs renderer failed.", render_result, warnings)
    pptx_path = str(render_result.get("output") or "")
    result = {
        "ok": True,
        "mode": "pptx",
        "presentation_id": None,
        "url": None,
        "file_token": None,
        "pptx_path": pptx_path,
        "ppt_draft": draft,
        "render_result": render_result,
        "upload_result": None,
        "warnings": warnings,
    }
    if options.get("dry_run", True):
        warnings.append("Dry run: rendered PPTX locally without uploading to Feishu Drive.")
        return result

    folder_token = extract_folder_token(options.get("folder_token") or env_value("FEISHU_DOC_FOLDER_TOKEN"))
    if not folder_token:
        warnings.append("FEISHU_FOLDER_TOKEN_MISSING; rendered PPTX locally but did not upload.")
        return result
    upload_result = _upload_pptx_to_drive(pptx_path, folder_token, str(options.get("as") or "user"), draft["title"])
    result["upload_result"] = upload_result
    data = upload_result.get("json", {}).get("data") or upload_result.get("json", {})
    result["file_token"] = data.get("file_token") or data.get("token")
    result["url"] = data.get("url") or data.get("open_url")
    if upload_result.get("ok") is not True:
        warnings.append("PPTX rendered locally but upload to Feishu Drive failed.")
    return result


def _render_pptx_with_node(draft: dict, options: dict) -> dict:
    repo_root = Path(__file__).resolve().parents[3]
    renderer_path = repo_root / "packages" / "pptx_renderer" / "render-deck.mjs"
    output_dir = repo_root / ".artifacts" / "pptx"
    safe_title = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", str(draft.get("title") or "presentation")).strip("_")[:60]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    input_path = output_dir / f"{stamp}-{safe_title or 'presentation'}.json"
    output_path = output_dir / f"{stamp}-{safe_title or 'presentation'}.pptx"
    asset_root = Path(str(options.get("asset_root") or repo_root)).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    input_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    command = [
        "node",
        str(renderer_path),
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--asset-root",
        str(asset_root),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(options.get("pptx_timeout_seconds") or 120),
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "command": command}
    data = parse_json_object(completed.stdout) or parse_json_object(completed.stderr)
    if completed.returncode != 0:
        return {
            "ok": False,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "json": data,
            "command": command,
        }
    return {
        "ok": True,
        "output": str(output_path),
        "input": str(input_path),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "json": data,
        "bytes": output_path.stat().st_size if output_path.is_file() else 0,
    }


def _upload_pptx_to_drive(pptx_path: str, folder_token: str, identity: str, title: str) -> dict:
    name = f"{str(title or 'Generated Presentation').strip() or 'Generated Presentation'}.pptx"
    repo_root = Path(__file__).resolve().parents[3]
    absolute = Path(pptx_path).resolve()
    try:
        relative = absolute.relative_to(repo_root)
    except ValueError:
        relative = absolute
    completed = subprocess.run(
        [
            lark_cli_path(),
            "drive",
            "+upload",
            "--file",
            str(relative),
            "--name",
            name,
            "--folder-token",
            folder_token,
            "--as",
            identity,
        ],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "json": parse_json_object(completed.stdout),
        "file_arg": str(relative),
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


def _metric_items(block: dict) -> list[dict]:
    raw_items = block.get("items") or block.get("metrics") or []
    items = []
    for item in raw_items if isinstance(raw_items, list) else []:
        if isinstance(item, dict):
            items.append(
                {
                    "label": str(item.get("label") or item.get("name") or item.get("title") or ""),
                    "value": str(item.get("value") or ""),
                    "note": str(item.get("note") or item.get("target") or item.get("definition") or ""),
                }
            )
    return items


def _cards(block: dict) -> list[dict]:
    cards = []
    for card in block.get("cards") or []:
        if isinstance(card, dict):
            cards.append(
                {
                    "title": str(card.get("title") or card.get("label") or ""),
                    "body": str(card.get("body") or card.get("description") or card.get("text") or ""),
                }
            )
    return cards


def _timeline_events(block: dict) -> list[dict]:
    raw_events = block.get("events") or block.get("items") or []
    events = []
    for event in raw_events if isinstance(raw_events, list) else []:
        if isinstance(event, dict):
            events.append(
                {
                    "date": str(event.get("date") or event.get("time") or event.get("phase") or ""),
                    "title": str(event.get("title") or event.get("label") or ""),
                    "body": str(event.get("body") or event.get("description") or event.get("text") or ""),
                }
            )
    return events


def _slide_to_xml(slide: dict, theme: dict | None = None, page: int = 1, total: int = 1) -> str:
    theme = theme or {}
    layout = str(slide.get("layout") or "")
    content = slide.get("content") if isinstance(slide.get("content"), dict) else {}
    tokens = theme_tokens(theme)
    background_hex = _slide_background_hex(layout, content, tokens)
    render_tokens = dict(tokens)
    render_tokens["body_title"] = readable_text_color(background_hex, tokens["body_title"])
    render_tokens["body_text"] = readable_text_color(background_hex, tokens["body_text"])
    render_tokens["body_muted"] = readable_text_color(background_hex, tokens["body_muted"])
    background = _rgb_hex(background_hex)
    text_color = render_tokens["body_text"]
    shapes = [_background_shape(background), _title_shape(str(slide.get("title") or ""), text_color)]

    if layout == "cover":
        shapes = [_background_shape(background), *_cover_shapes(slide, content, tokens)]
    elif layout == "section_divider":
        shapes = [_background_shape(background), *_section_shapes(slide, content, render_tokens)]
    elif layout == "metric_cards":
        shapes.extend(_content_theme_chrome(tokens))
        shapes.extend(_metric_card_shapes(content, render_tokens))
    elif layout in {"problem_cards", "cards"}:
        shapes.extend(_content_theme_chrome(tokens))
        shapes.extend(_card_grid_shapes(content.get("problems") or content.get("cards") or [], render_tokens))
    elif layout in {"three_stage_flow", "flow"}:
        shapes.extend(_content_theme_chrome(tokens))
        shapes.extend(_flow_shapes(content, render_tokens))
    elif layout == "timeline":
        shapes.extend(_content_theme_chrome(tokens))
        shapes.extend(_timeline_shapes(content, render_tokens))
    elif layout in {"risk_table", "comparison_table", "table"}:
        shapes.extend(_content_theme_chrome(tokens))
        shapes.extend(_table_shapes(content, render_tokens))
    elif layout == "summary_next_steps":
        shapes.extend(_content_theme_chrome(tokens))
        shapes.extend(_summary_shapes(content, render_tokens))
    else:
        shapes.extend(_content_theme_chrome(tokens))
        shapes.append(_text_shape(90, 170, 780, 320, _slide_body_lines(slide), "body", color=text_color))

    if len(shapes) == 2:
        shapes.append(_text_shape(90, 170, 780, 320, _content_summary_lines(content), "body", color=text_color))
    footer_color = tokens["cover_muted"] if layout == "cover" else readable_text_color(background_hex, tokens["body_muted"])
    shapes.append(_footer_shape(page, total, footer_color))
    return (
        '<slide xmlns="http://www.larkoffice.com/sml/2.0">'
        f'<style><fill><fillColor color="{background}"/></fill></style>'
        "<data>"
        + "".join(shapes)
        + "</data>"
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
        return [f"{card.get('title', '')}: {card.get('body') or card.get('description', '')}" for card in content.get("cards") or [] if isinstance(card, dict)]
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


def _cover_shapes(slide: dict, content: dict, tokens: dict) -> list[str]:
    accent = _rgb_hex(tokens["accent"])
    accent2 = _rgb_hex(tokens["accent2"])
    subtitle = str(content.get("subtitle") or content.get("tagline") or "")
    kicker = str(content.get("kicker") or content.get("audience") or "")
    return [
        _rect_shape(0, 0, 960, 18, accent),
        _rect_shape(0, 18, 320, 8, accent2),
        _rect_shape(70, 72, 12, 360, accent),
        _text_shape(105, 120, 720, 130, [str(slide.get("title") or "")], "title", color=tokens["cover_title"], font_size=36),
        _text_shape(108, 275, 700, 80, [subtitle], "body", color=tokens["cover_subtitle"], font_size=18),
        _text_shape(108, 390, 420, 50, [kicker], "caption", color=tokens["cover_muted"], font_size=12),
    ]


def _section_shapes(slide: dict, content: dict, tokens: dict) -> list[str]:
    accent = _rgb_hex(tokens["accent"])
    points = _string_items(content.get("points") or content.get("bullets") or [])
    if points:
        return _section_points_shapes(slide, points, tokens)
    return [
        _rect_shape(0, 0, 960, 540, accent),
        _text_shape(85, 150, 760, 110, [str(slide.get("title") or "")], "title", color=tokens["cover_title"], font_size=36),
    ]


def _section_points_shapes(slide: dict, points: list[str], tokens: dict) -> list[str]:
    accent = _rgb_hex(tokens["accent"])
    accent2 = _rgb_hex(tokens["accent2"])
    shapes = [
        _title_shape(str(slide.get("title") or ""), tokens["body_title"]),
        _rect_shape(90, 160, 8, 230, accent),
    ]
    for index, point in enumerate(points[:6]):
        col = index % 2
        row = index // 2
        x = 120 + col * 390
        y = 165 + row * 78
        tone = accent2 if index % 2 else accent
        shapes.append(_rect_shape(x, y, 28, 28, tone))
        shapes.append(_text_shape(x + 44, y - 4, 310, 54, [point], "body", color=tokens["body_text"]))
    return shapes


def _content_theme_chrome(tokens: dict) -> list[str]:
    accent = _rgb_hex(tokens["accent"])
    accent2 = _rgb_hex(tokens["accent2"])
    return [
        _rect_shape(0, 0, 18, 540, accent),
        _rect_shape(70, 135, 220, 7, accent2),
    ]


def _slide_background_hex(layout: str, content: dict, tokens: dict) -> str:
    if layout == "cover":
        return tokens["cover_bg"]
    if layout == "section_divider" and not (content.get("points") or content.get("bullets")):
        return tokens["accent"]
    return tokens["background"]


def normalize_hex_color(color: str, fallback: str) -> str:
    text = str(color or "").strip()
    match = re.match(r"^#?([0-9A-Fa-f]{6})$", text)
    if not match:
        fallback_text = str(fallback or "").strip()
        match = re.match(r"^#?([0-9A-Fa-f]{6})$", fallback_text)
    return match.group(1).upper() if match else "0F172A"


def relative_luminance(hex_color: str) -> float:
    normalized = normalize_hex_color(hex_color, "0F172A")
    channels = [int(normalized[i : i + 2], 16) / 255 for i in (0, 2, 4)]

    def linearize(value: float) -> float:
        if value <= 0.03928:
            return value / 12.92
        return ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = [linearize(channel) for channel in channels]
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def readable_text_color(background_hex: str, prefer: str | None = None) -> str:
    background = normalize_hex_color(background_hex, "FFFFFF")
    bg_luminance = relative_luminance(background)
    if bg_luminance < 0.42:
        return "FFFFFF"

    preferred = normalize_hex_color(prefer or "0F172A", "0F172A")
    preferred_contrast = _contrast_ratio(background, preferred)
    if preferred_contrast >= 4.5:
        return preferred
    dark_contrast = _contrast_ratio(background, "0F172A")
    light_contrast = _contrast_ratio(background, "FFFFFF")
    return "0F172A" if dark_contrast >= light_contrast else "FFFFFF"


def theme_tokens(theme: dict) -> dict[str, str]:
    raw = theme if isinstance(theme, dict) else {}
    accent = normalize_hex_color(str(raw.get("accent") or ""), "2563EB")
    accent2 = normalize_hex_color(str(raw.get("accent2") or ""), "0F766E")
    background = normalize_hex_color(str(raw.get("background") or ""), "F8FAFC")
    surface = normalize_hex_color(str(raw.get("surface") or ""), "FFFFFF")
    text = normalize_hex_color(str(raw.get("text") or ""), "0F172A")
    muted = normalize_hex_color(str(raw.get("muted") or ""), "64748B")
    cover_bg = normalize_hex_color(str(raw.get("cover_bg") or raw.get("text") or ""), text)
    if relative_luminance(cover_bg) >= 0.42:
        cover_bg = text if relative_luminance(text) < 0.42 else "0F172A"
    return {
        "accent": accent,
        "accent2": accent2,
        "background": background,
        "surface": surface,
        "text": text,
        "muted": muted,
        "warning": normalize_hex_color(str(raw.get("warning") or ""), "F97316"),
        "cover_bg": cover_bg,
        "cover_title": "FFFFFF",
        "cover_subtitle": "E2E8F0",
        "cover_muted": "CBD5E1",
        "body_title": normalize_hex_color(str(raw.get("body_title") or ""), text),
        "body_text": normalize_hex_color(str(raw.get("body_text") or ""), text),
        "body_muted": normalize_hex_color(str(raw.get("body_muted") or ""), muted),
    }


def _contrast_ratio(first_hex: str, second_hex: str) -> float:
    first = relative_luminance(first_hex)
    second = relative_luminance(second_hex)
    lighter = max(first, second)
    darker = min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _background_shape(color: str) -> str:
    return _rect_shape(0, 0, 960, 540, color)


def _metric_card_shapes(content: dict, tokens: dict) -> list[str]:
    items = _content_metrics(content)
    shapes = []
    surface_text = readable_text_color(tokens["surface"], tokens["text"])
    surface_muted = readable_text_color(tokens["surface"], tokens["muted"])
    for index, item in enumerate(items[:4]):
        x = 80 + index * 210
        tone = tokens["accent2"] if index % 2 else tokens["accent"]
        shapes.append(_rect_shape(x, 168, 180, 176, _rgb_hex(tokens["surface"])))
        shapes.append(_rect_shape(x, 168, 180, 10, _rgb_hex(tone)))
        shapes.append(_text_shape(x + 18, 194, 145, 42, [str(item.get("label") or "")], "caption", color=surface_muted))
        shapes.append(_text_shape(x + 18, 242, 145, 58, [str(item.get("value") or "")], "metric", color=tokens["accent"], font_size=28))
        shapes.append(_text_shape(x + 18, 306, 145, 34, [str(item.get("note") or "")], "small", color=surface_muted))
    return shapes or [_text_shape(90, 170, 780, 260, _string_items(content.get("items") or []), "body", color=tokens["body_text"])]


def _card_grid_shapes(cards: Any, tokens: dict) -> list[str]:
    normalized = _content_cards(cards)
    shapes = []
    surface_text = readable_text_color(tokens["surface"], tokens["text"])
    positions = [(80, 160), (360, 160), (640, 160), (80, 345), (360, 345), (640, 345)]
    for index, card in enumerate(normalized[:6]):
        x, y = positions[index]
        tone = tokens["warning"] if index == 0 else tokens["accent2"] if index % 2 else tokens["accent"]
        shapes.append(_rect_shape(x, y, 240, 145, _rgb_hex(tokens["surface"])))
        shapes.append(_rect_shape(x, y, 8, 145, _rgb_hex(tone)))
        shapes.append(_text_shape(x + 20, y + 18, 195, 46, [str(card.get("title") or "")], "subtitle", color=tokens["accent"]))
        shapes.append(_text_shape(x + 20, y + 72, 195, 58, [str(card.get("body") or "")], "small", color=surface_text))
    return shapes


def _flow_shapes(content: dict, tokens: dict) -> list[str]:
    steps = (
        content.get("steps")
        or content.get("nodes")
        or content.get("phases")
        or content.get("items")
        or content.get("roadmap")
        or content.get("process")
        or []
    )
    normalized = _content_cards(steps)
    shapes = []
    surface_text = readable_text_color(tokens["surface"], tokens["text"])
    count = min(len(normalized), 4)
    if not count:
        return shapes
    card_w = 178 if count == 4 else 220
    gap = 40 if count == 4 else 58
    total_w = count * card_w + (count - 1) * gap
    start_x = max(60, int((960 - total_w) / 2))
    y = 198
    for index, step in enumerate(normalized[:4]):
        x = start_x + index * (card_w + gap)
        tone = tokens["accent2"] if index % 2 else tokens["accent"]
        shapes.append(_rect_shape(x, y, card_w, 148, _rgb_hex(tokens["surface"])))
        shapes.append(_rect_shape(x, y, card_w, 12, _rgb_hex(tone)))
        shapes.append(_text_shape(x + 16, y + 24, card_w - 32, 58, [str(step.get("title") or f"Step {index + 1}")], "subtitle", color=tokens["accent"]))
        shapes.append(_text_shape(x + 16, y + 88, card_w - 32, 48, [str(step.get("body") or "")], "small", color=surface_text))
        if index < count - 1:
            shapes.append(_text_shape(x + card_w + 8, y + 57, max(24, gap - 16), 30, ["->"], "subtitle", color=tokens["accent"]))
    return shapes


def _timeline_shapes(content: dict, tokens: dict) -> list[str]:
    events = _content_events(content)
    shapes = [_rect_shape(95, 265, 760, 5, _rgb_hex(tokens["accent"]))]
    for index, event in enumerate(events[:5]):
        x = 95 + index * 180
        tone = tokens["accent2"] if index % 2 else tokens["accent"]
        shapes.append(_rect_shape(x, 252, 28, 28, _rgb_hex(tone)))
        shapes.append(_text_shape(x - 20, 295, 130, 30, [str(event.get("date") or "")], "caption", color=tokens["body_muted"]))
        shapes.append(_text_shape(x - 20, 330, 145, 42, [str(event.get("title") or "")], "subtitle", color=tokens["body_title"]))
        shapes.append(_text_shape(x - 20, 380, 145, 50, [str(event.get("body") or "")], "small", color=tokens["body_text"]))
    return shapes


def _table_shapes(content: dict, tokens: dict) -> list[str]:
    columns = content.get("columns") or content.get("headers") or []
    rows = content.get("rows") or content.get("risks") or content.get("comparisons") or []
    table_rows = [columns, *rows] if columns else rows
    shapes = []
    y = 150
    for row_index, row in enumerate(table_rows[:6]):
        cells = row if isinstance(row, list) else list(row.values()) if isinstance(row, dict) else [row]
        x = 70
        fill_hex = tokens["accent"] if row_index == 0 else tokens["surface"]
        fill = _rgb_hex(fill_hex)
        text_type = "body" if row_index == 0 else "small"
        color = readable_text_color(fill_hex, tokens["body_text"])
        width = max(1, int(820 / max(1, len(cells))))
        for cell in cells[:4]:
            shapes.append(_rect_shape(x, y, width - 6, 54, fill))
            shapes.append(_text_shape(x + 10, y + 12, width - 26, 28, [str(cell)], text_type, color=color))
            x += width
        y += 62
    return shapes


def _summary_shapes(content: dict, tokens: dict) -> list[str]:
    points = _string_items(content.get("next_steps") or content.get("points") or content.get("bullets") or [])
    outcomes = _string_items(content.get("outcomes") or content.get("expected_outcomes") or [])
    shapes = [
        _rect_shape(74, 155, 6, 270, _rgb_hex(tokens["accent"])),
        _text_shape(90, 155, 350, 45, ["Next steps"], "subtitle", color=tokens["accent"]),
        _text_shape(90, 215, 350, 210, points[:5], "body", color=tokens["body_text"]),
        _rect_shape(504, 155, 6, 270, _rgb_hex(tokens["accent2"])),
        _text_shape(520, 155, 350, 45, ["Expected outcomes"], "subtitle", color=tokens["accent2"]),
        _text_shape(520, 215, 350, 210, outcomes[:5], "body", color=tokens["body_text"]),
    ]
    return shapes


def _content_summary_lines(content: dict) -> list[str]:
    lines: list[str] = []
    for key, value in content.items():
        if isinstance(value, list):
            for item in value[:5]:
                if isinstance(item, dict):
                    title = item.get("title") or item.get("label") or item.get("name") or item.get("date") or item.get("time")
                    body = item.get("body") or item.get("description") or item.get("text") or item.get("value") or item.get("note")
                    line = " - ".join(str(part) for part in (title, body) if str(part or "").strip())
                    if line:
                        lines.append(line)
                elif str(item).strip():
                    lines.append(str(item).strip())
        elif isinstance(value, str) and value.strip():
            lines.append(value.strip())
    return lines[:8]


def _title_shape(title: str, color: str) -> str:
    return _text_shape(70, 55, 820, 74, [title], "title", color=color, font_size=32)


def _footer_shape(page: int, total: int, color: str) -> str:
    return _text_shape(760, 500, 130, 22, [f"{page}/{total}"], "caption", color=color, font_size=11)


def _text_shape(
    x: int,
    y: int,
    width: int,
    height: int,
    lines: list[str],
    text_type: str,
    color: str | None = None,
    font_size: int | None = None,
) -> str:
    paragraphs = _paragraphs_xml(lines, color=color, font_size=font_size)
    if not paragraphs:
        return ""
    return (
        f'<shape type="text" topLeftX="{x}" topLeftY="{y}" width="{width}" height="{height}">'
        f'<content textType="{text_type}">{paragraphs}</content>'
        "</shape>"
    )


def _rect_shape(x: int, y: int, width: int, height: int, color: str) -> str:
    return (
        f'<shape type="rect" topLeftX="{x}" topLeftY="{y}" width="{width}" height="{height}">'
        f'<style><fill><fillColor color="{color}"/></fill></style>'
        "<content><p></p></content>"
        "</shape>"
    )


def _paragraphs_xml(lines: list[str], color: str | None = None, font_size: int | None = None) -> str:
    clean = [line for line in lines if str(line).strip()]
    if not clean:
        return ""
    style = _span_style(color=color, font_size=font_size)
    if not style:
        return "".join(f"<p>{escape(str(line))}</p>" for line in clean)
    return "".join(f'<p><span style="{style}">{escape(str(line))}</span></p>' for line in clean)


def _span_style(color: str | None = None, font_size: int | None = None) -> str:
    parts = []
    if font_size:
        parts.append(f"font-size:{font_size}px")
    if color:
        parts.append(f"color:{_rgb_hex(color)}")
    return ";".join(parts)


def _rgb_hex(value: Any) -> str:
    return _rgb(normalize_hex_color(str(value or ""), "F8FAFC"))


def _hex_color(value: Any, default: str) -> str:
    return f"#{normalize_hex_color(str(value or ''), str(default or 'F8FAFC'))}"


def _rgb(value: Any) -> str:
    text = str(value or "").strip()
    match = None
    if re_match := re.match(r"^#?([0-9A-Fa-f]{6})$", text):
        match = re_match.group(1)
    if not match:
        return "rgb(248,250,252)"
    red = int(match[0:2], 16)
    green = int(match[2:4], 16)
    blue = int(match[4:6], 16)
    return f"rgb({red},{green},{blue})"


def _string_items(value: Any) -> list[str]:
    if isinstance(value, list):
        output = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("title") or item.get("label") or item.get("body") or item.get("description")
                if item.get("value"):
                    text = f"{text}: {item.get('value')}" if text else item.get("value")
                output.append(str(text or ""))
            else:
                output.append(str(item))
        return [item for item in output if item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _content_metrics(content: dict) -> list[dict]:
    raw_items = content.get("metrics") or content.get("items") or content.get("cards") or []
    return _metric_items({"items": raw_items})


def _content_cards(value: Any) -> list[dict]:
    raw_cards = value if isinstance(value, list) else []
    return _cards({"cards": raw_cards})


def _content_events(content: dict) -> list[dict]:
    return _timeline_events({"events": content.get("events") or content.get("items") or content.get("milestones") or []})

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
