from __future__ import annotations

import json
import re
import time
from datetime import date
from typing import Any

import httpx

from services.agent.config import IR_SCHEMA_VERSION, get_agent_settings
from services.agent.request_normalizer import normalize_agent_ir_request

JsonDict = dict[str, Any]

ALLOWED_BLOCK_KINDS = {
    "cover",
    "split",
    "flow",
    "metrics",
    "cards",
    "table",
    "timeline",
    "image",
}

PLANB_IR_PROMPT = """You are the only Agent in PlanB.

Use all scope messages in the input to write one complete platform-neutral
industrial Artifact IR. Return JSON only.

Rules:
- Do not produce topic summaries, task summaries, JSON Patch, Feishu OpenAPI
  payloads, files, or Markdown.
- schemaVersion must be exactly "0.2.0".
- blocks must be non-empty and use only these kinds: cover, split, flow,
  metrics, cards, table, timeline, image.
- Return {"ir": {...}, "warnings": ["..."]}.
"""


def generate_ir_from_messages(request: dict) -> dict:
    started_at = time.perf_counter()
    normalized = normalize_agent_ir_request(request)
    warnings = list(normalized.get("warnings") or [])
    if normalized.get("ok") is False:
        return _finish("normalize_request", normalized, started_at)

    payload = normalized["request"]
    llm_result = _call_llm_for_ir(payload)
    warnings.extend(llm_result.get("warnings") or [])
    if llm_result.get("ok") is not True:
        return _finish("generate_ir", llm_result, started_at)

    ir = _extract_ir(llm_result.get("data") or {})
    ir = _ensure_ir_defaults(ir, payload)
    validation = _validate_ir(ir)
    if validation:
        return _finish(
            "generate_ir",
            _error("AGENT_IR_INVALID", "LLM returned invalid IR.", validation, warnings),
            started_at,
            debug=llm_result.get("debug"),
        )

    return _finish(
        "done",
        {
            "ok": True,
            "ir": ir,
            "warnings": _dedupe(warnings),
            "debug": llm_result.get("debug") or {},
        },
        started_at,
    )


def _call_llm_for_ir(payload: JsonDict) -> JsonDict:
    settings = get_agent_settings()
    started_at = time.perf_counter()
    debug = {
        "llm_provider": settings.provider,
        "llm_model": settings.model,
    }
    missing = [
        name
        for name, value in (
            ("AGENT_LLM_BASE_URL", settings.base_url),
            ("AGENT_LLM_API_KEY", settings.api_key),
            ("AGENT_LLM_MODEL", settings.model),
        )
        if not str(value or "").strip()
    ]
    if missing:
        return _error(
            "AGENT_LLM_CONFIG_MISSING",
            "Agent LLM configuration is incomplete.",
            missing,
            debug={**debug, "llm_elapsed_ms": _elapsed_ms(started_at)},
        )

    try:
        response = httpx.post(
            f"{settings.base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.model,
                "messages": [
                    {"role": "system", "content": PLANB_IR_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "temperature": settings.temperature,
                "max_tokens": settings.max_tokens,
                "response_format": {"type": "json_object"},
            },
            timeout=settings.timeout_seconds,
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _error(
            "AGENT_LLM_CALL_FAILED",
            "Agent LLM call failed.",
            str(exc),
            debug={**debug, "llm_elapsed_ms": _elapsed_ms(started_at)},
        )

    try:
        body = response.json()
        content = str(body["choices"][0]["message"]["content"])
        data = _parse_json_object(content)
    except Exception as exc:  # noqa: BLE001
        return _error(
            "AGENT_LLM_INVALID_RESPONSE",
            "Agent LLM response is not valid chat-completions JSON.",
            str(exc),
            debug={**debug, "llm_elapsed_ms": _elapsed_ms(started_at)},
        )
    if not data:
        return _error(
            "AGENT_LLM_INVALID_JSON",
            "Agent LLM response content is not a JSON object.",
            [],
            debug={**debug, "llm_elapsed_ms": _elapsed_ms(started_at)},
        )
    return {
        "ok": True,
        "data": data,
        "warnings": _as_list(data.get("warnings")),
        "debug": {**debug, "llm_elapsed_ms": _elapsed_ms(started_at)},
    }


def _extract_ir(data: JsonDict) -> JsonDict:
    if isinstance(data.get("ir"), dict):
        return data["ir"]
    if data.get("schemaVersion") == IR_SCHEMA_VERSION:
        return data
    return {}


def _ensure_ir_defaults(ir: JsonDict, request: JsonDict) -> JsonDict:
    if not isinstance(ir, dict):
        return {}
    task = _as_dict(request.get("task"))
    out = json.loads(json.dumps(ir, ensure_ascii=False))
    out["schemaVersion"] = IR_SCHEMA_VERSION
    out.setdefault("docId", f"{_safe_id(str(task.get('task_id') or 'task'))}_ir")

    meta = out.setdefault("meta", {})
    if not isinstance(meta, dict):
        meta = {}
        out["meta"] = meta
    meta.setdefault("title", str(task.get("title") or "").strip())
    meta.setdefault("subtitle", str(task.get("goal") or "").strip())
    meta.setdefault("owner", "Agent")
    meta.setdefault("date", date.today().isoformat())
    meta.setdefault("audience", str(task.get("audience") or "").strip())

    theme = out.setdefault("theme", {})
    if not isinstance(theme, dict):
        theme = {}
        out["theme"] = theme
    for key, value in _default_theme().items():
        theme.setdefault(key, value)

    if not isinstance(out.get("assets"), dict):
        out["assets"] = {}
    blocks = out.get("blocks")
    if not isinstance(blocks, list):
        out["blocks"] = []
        blocks = out["blocks"]
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        block.setdefault("id", f"block_{index + 1}")
        block.setdefault("title", str(block.get("id") or f"Block {index + 1}"))
    return out


def _validate_ir(ir: JsonDict) -> list[str]:
    errors: list[str] = []
    if not isinstance(ir, dict):
        return ["IR must be an object."]
    if ir.get("schemaVersion") != IR_SCHEMA_VERSION:
        errors.append('schemaVersion must be "0.2.0".')
    meta = ir.get("meta")
    if not isinstance(meta, dict) or not str(meta.get("title") or "").strip():
        errors.append("meta.title is required.")
    blocks = ir.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        errors.append("blocks must be a non-empty array.")
        return errors
    seen: set[str] = set()
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            errors.append(f"blocks[{index}] must be an object.")
            continue
        block_id = str(block.get("id") or "").strip()
        kind = str(block.get("kind") or "").strip()
        title = str(block.get("title") or "").strip()
        if not block_id or not kind or not title:
            errors.append(f"blocks[{index}] must contain id, kind, and title.")
        if block_id in seen:
            errors.append(f"Duplicate block id: {block_id}.")
        seen.add(block_id)
        if kind not in ALLOWED_BLOCK_KINDS:
            errors.append(f"Unsupported block kind: {kind}.")
    return errors


def _parse_json_object(raw: str) -> JsonDict:
    try:
        value = json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
        except Exception:
            return {}
    return value if isinstance(value, dict) else {}


def _default_theme() -> dict[str, str]:
    return {
        "name": "Executive Blue",
        "accent": "#2F6BFF",
        "accent2": "#7C3AED",
        "background": "#F8FAFC",
        "surface": "#FFFFFF",
        "text": "#0F172A",
        "muted": "#64748B",
        "success": "#16A34A",
        "warning": "#F97316",
        "fontFace": "Aptos",
    }


def _finish(stage: str, payload: JsonDict, started_at: float, debug: JsonDict | None = None) -> JsonDict:
    output = dict(payload)
    output.setdefault("stage", stage)
    if debug:
        output["debug"] = {**debug, **_as_dict(output.get("debug"))}
    output.setdefault("elapsed_ms", _elapsed_ms(started_at))
    return output


def _error(
    code: str,
    message: str,
    details: Any | None = None,
    warnings: list[str] | None = None,
    debug: JsonDict | None = None,
) -> JsonDict:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    result = {"ok": False, "error": error, "warnings": warnings or []}
    if debug:
        result["debug"] = debug
    return result


def _as_dict(value: Any) -> JsonDict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_\-]+", "_", value.strip())
    return safe.strip("_") or "task"


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 3)


def _dedupe(items: list[Any]) -> list[Any]:
    output = []
    seen = set()
    for item in items:
        marker = repr(item)
        if marker not in seen:
            seen.add(marker)
            output.append(item)
    return output


__all__ = ["generate_ir_from_messages"]
