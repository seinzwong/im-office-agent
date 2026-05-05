from __future__ import annotations

from typing import Any

from .feishu_board_adapter import ir_to_feishu_board_draft, publish_ir_to_feishu_board
from .feishu_doc_adapter import ir_to_feishu_doc_blocks, publish_ir_to_feishu_doc
from .ir_normalizer import normalize_agent_ir_output
from .ir_schema import ensure_ir_defaults, validate_ir
from .markdown_adapter import ir_to_markdown
from .ppt_adapter import ir_to_ppt_draft, publish_ir_to_ppt


def publish_ir(ir: dict, options: dict) -> dict:
    warnings: list[str] = []
    normalized = ensure_ir_defaults(ir)
    validation = validate_ir(normalized)
    if validation:
        return _error("IR_VALIDATION_FAILED", "IR validation failed", validation, warnings, ir_validation=validation)

    options = options or {}
    targets = options.get("targets") or ["doc"]
    if isinstance(targets, str):
        targets = [targets]
    if "all" in targets:
        targets = ["doc", "board", "ppt"]
    target_results: dict[str, dict] = {}
    for target in targets:
        if target == "doc":
            doc_options = {**options.get("doc", {}), "dry_run": options.get("dry_run", True)}
            if "board" in targets:
                doc_options.setdefault("include_whiteboard", True)
            target_results["doc"] = publish_ir_to_feishu_doc(normalized, doc_options)
        elif target == "board":
            target_results["board"] = publish_ir_to_feishu_board(normalized, {**options.get("board", {}), "dry_run": options.get("dry_run", True)})
        elif target == "ppt":
            target_results["ppt"] = publish_ir_to_ppt(normalized, {**options.get("ppt", {}), "dry_run": options.get("dry_run", True)})
        else:
            target_results[str(target)] = _error("INVALID_PUBLISH_MODE", f"Unsupported target: {target}", [target], [])
    for result in target_results.values():
        warnings.extend(result.get("warnings") or [])
    return {
        "ok": all(result.get("ok") is True for result in target_results.values()),
        "ir_validation": validation,
        "markdown_preview": ir_to_markdown(normalized),
        "doc_blocks_preview": ir_to_feishu_doc_blocks(normalized),
        "board_draft": ir_to_feishu_board_draft(normalized),
        "ppt_draft": ir_to_ppt_draft(normalized),
        "targets": target_results,
        "warnings": _dedupe(warnings),
    }


def publish_agent_output(agent_output: dict, options: dict, current_ir: dict | None = None) -> dict:
    normalized = normalize_agent_ir_output(agent_output, current_ir)
    if isinstance(normalized, dict) and normalized.get("ok") is False:
        return normalized
    return publish_ir(normalized, options)


def build_ir_preview(ir: dict) -> dict:
    normalized = ensure_ir_defaults(ir)
    errors = validate_ir(normalized)
    if errors:
        return _error("IR_VALIDATION_FAILED", "IR validation failed", errors, [], ir_validation=errors)
    return {
        "ok": True,
        "markdown": ir_to_markdown(normalized),
        "doc_blocks": ir_to_feishu_doc_blocks(normalized),
        "board_draft": ir_to_feishu_board_draft(normalized),
        "ppt_draft": ir_to_ppt_draft(normalized),
        "warnings": [],
    }


def _error(
    code: str,
    message: str,
    details: Any | None = None,
    warnings: list[str] | None = None,
    **extra: Any,
) -> dict:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"ok": False, "error": error, "warnings": warnings or [], **extra}


def _dedupe(items: list[Any]) -> list[Any]:
    output = []
    seen = set()
    for item in items:
        marker = repr(item)
        if marker not in seen:
            seen.add(marker)
            output.append(item)
    return output
