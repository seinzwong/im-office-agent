from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from services.agent.agents import validate_slide_draft

from .ir_schema import ensure_ir_defaults, validate_ir
from .runtime import adapter_error, env_value, extract_folder_token, lark_cli_path, parse_json_object


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
                "layout": str(slide.get("layout") or "summary"),
                "title": str(slide.get("title") or ""),
                "content": content,
                "visual": slide.get("visual") if isinstance(slide.get("visual"), dict) else {},
                "asset_key": str(slide.get("asset_key") or slide.get("assetKey") or slide.get("image") or ""),
                "speaker_notes": str(slide.get("speaker_notes") or ""),
            }
        )
    return {
        "title": str(draft.get("title") or "Generated Presentation"),
        "file_name": str(draft.get("file_name") or draft.get("title") or "Generated Presentation"),
        "subtitle": str(draft.get("subtitle") or ""),
        "theme": draft.get("theme") if isinstance(draft.get("theme"), dict) else {},
        "assets": draft.get("assets") if isinstance(draft.get("assets"), dict) else {},
        "slides": slides,
    }


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
    slide_errors = validate_slide_draft(slide_draft)
    if slide_errors:
        return adapter_error("SLIDE_DRAFT_VALIDATION_FAILED", "SlideDraft validation failed", slide_errors, warnings)
    draft = slide_draft_to_ppt_draft(slide_draft)
    options = options or {}
    mode = str(options.get("mode") or "create")
    if mode != "create":
        return adapter_error("INVALID_PUBLISH_MODE", "PPT mode must be create.", [mode], warnings)
    renderer = str(options.get("renderer") or options.get("ppt_renderer") or "pptx").lower()
    if renderer != "pptx":
        return adapter_error("UNSUPPORTED_PPT_RENDERER", "PPT renderer must be pptx.", [renderer], warnings)
    if "fallback_to_xml" in options:
        return adapter_error("UNSUPPORTED_PPT_RENDERER", "PPT XML fallback is no longer supported.", ["fallback_to_xml"], warnings)
    return _publish_pptx_draft(draft, options, warnings)


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
    upload_result = _upload_pptx_to_drive(pptx_path, folder_token, str(options.get("as") or "user"), str(draft.get("file_name") or draft["title"]))
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
    safe_title = _safe_file_stem(str(draft.get("file_name") or draft.get("title") or "presentation"))
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
    name = f"{_safe_file_stem(title or 'Generated Presentation')}.pptx"
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


def _safe_file_stem(value: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", str(value or "").strip())
    safe = re.sub(r"\s+", " ", safe).strip(" ._")
    return safe[:60] or "Generated Presentation"
