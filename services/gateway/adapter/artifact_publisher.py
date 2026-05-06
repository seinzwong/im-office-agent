from __future__ import annotations

from typing import Any

from .feishu_board_adapter import publish_ir_to_feishu_board
from .feishu_doc_adapter import publish_ir_to_feishu_doc
from .ir_schema import ensure_ir_defaults, validate_ir
from .ppt_adapter import publish_ir_to_ppt


def publish_ir(
    ir: dict,
    publish_context: dict | None = None,
    options: dict | None = None,
) -> dict:
    warnings: list[str] = []
    normalized = ensure_ir_defaults(ir)
    validation = validate_ir(normalized)
    if validation:
        return _error(
            "IR_VALIDATION_FAILED",
            "IR validation failed.",
            validation,
            warnings,
            stage="validate_ir",
            ir_validation=validation,
            publish_result={},
        )

    publish_context = publish_context or {}
    options = options or {}
    dry_run = bool(options.get("dry_run", True))
    targets = _normalize_targets(options.get("target_outputs") or options.get("targets") or ["doc"])
    folder_token = _first_non_empty(
        publish_context.get("folder_token"),
        options.get("folder_token"),
    )
    if not folder_token:
        warnings.append("FEISHU_FOLDER_TOKEN_MISSING")

    publish_result: dict[str, dict] = {}
    for target in targets:
        target_options = _target_options(options, publish_context, target, dry_run, folder_token)
        if target == "doc":
            if not dry_run and not folder_token:
                publish_result["doc"] = _error(
                    "FEISHU_FOLDER_TOKEN_MISSING",
                    "folder_token is required for real Feishu Doc publishing.",
                    [],
                    warnings=[],
                    stage="publish_doc",
                )
            else:
                publish_result["doc"] = publish_ir_to_feishu_doc(normalized, target_options)
        elif target == "board":
            if not dry_run:
                warnings.append("Board real publishing is TODO in PlanB; adapter may use existing lark-cli path.")
            publish_result["board"] = publish_ir_to_feishu_board(normalized, target_options)
        elif target == "ppt":
            if not dry_run:
                warnings.append("PPT real publishing is TODO in PlanB; adapter may use existing lark-cli path.")
            publish_result["ppt"] = publish_ir_to_ppt(normalized, target_options)
        else:
            publish_result[str(target)] = _error(
                "INVALID_TARGET_OUTPUT",
                f"Unsupported target output: {target}",
                [target],
                stage=f"publish_{target}",
            )

    for result in publish_result.values():
        warnings.extend(result.get("warnings") or [])

    return {
        "ok": all(result.get("ok") is True for result in publish_result.values()),
        "stage": "done",
        "publish_result": publish_result,
        "ir_validation": validation,
        "warnings": _dedupe(warnings),
    }


def _target_options(
    options: dict,
    publish_context: dict,
    target: str,
    dry_run: bool,
    folder_token: Any,
) -> dict:
    nested = options.get(target) if isinstance(options.get(target), dict) else {}
    merged = {
        **nested,
        "dry_run": dry_run,
    }
    if target == "ppt" and isinstance(options.get("slide_draft"), dict):
        merged["slide_draft"] = options["slide_draft"]
    if target == "board" and isinstance(options.get("board_ir"), dict):
        merged["board_ir"] = options["board_ir"]
    if folder_token:
        merged["folder_token"] = folder_token
    for key in ("user_access_token", "tenant_access_token", "authorized_user_id"):
        if publish_context.get(key):
            merged[key] = publish_context[key]
    return merged


def _normalize_targets(value: Any) -> list[str]:
    if isinstance(value, str):
        targets = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, list):
        targets = [str(item).strip() for item in value if str(item).strip()]
    else:
        targets = ["doc"]
    if "all" in targets:
        targets = ["doc", "board", "ppt"]
    targets = [target for target in targets if target in {"doc", "board", "ppt"}]
    return _dedupe(targets) or ["doc"]


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


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


__all__ = ["publish_ir"]
