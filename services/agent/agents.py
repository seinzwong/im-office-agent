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

CONTENT_IR_PROMPT = """You are the Content Agent in PlanB.

Use the scoped chat messages to produce one platform-neutral ContentIR for a
business solution. Return JSON only.

Rules:
- Return {"content_ir": {...}, "warnings": ["..."]}.
- Do not design slides, documents, whiteboards, XML, Markdown, or UI.
- content_ir must contain: title, subtitle, audience, background, problems,
  metrics, solution_modules, process, implementation_plan, risks,
  expected_outcomes, decision_points.
- Prefer facts and numbers from the messages. If a needed field is not explicit,
  infer conservatively and keep it concise.
"""

SLIDE_DRAFT_PROMPT = """You are the Slide Agent in PlanB.

Use the provided ContentIR to produce a high-quality SlideDraft for an executive
solution presentation. Return JSON only.

Rules:
- Return {"slide_draft": {...}, "warnings": ["..."]}.
- Do not invent new business facts beyond the ContentIR.
- slide_draft must contain title, subtitle, theme, and slides.
- Each slide must contain id, title, layout, content, and speaker_notes.
- Supported layouts: cover, section_divider, problem_cards, metric_cards,
  three_stage_flow, timeline, risk_table, comparison_table, summary_next_steps.
- Every slide content must be non-empty.
- section_divider may use an empty points array when the slide title itself is
  the section message.
- Use section_divider only for pure section breaks such as "Background",
  "Solution", or "Plan". Do not use section_divider for substantive content
  such as decision points, key actions, risks, milestones, or recommendations;
  use summary_next_steps, three_stage_flow, risk_table, timeline, or cards.
- Do not use the same layout for 3 consecutive slides.
- Produce 8 to 10 slides when enough content exists. Never produce more than
  10 slides.
- Choose a theme palette from the business content. For example, operational
  improvement can use blue/green, risk-heavy content can use blue/orange,
  growth content can use teal/indigo. Avoid leaving the default unchanged when
  the content suggests a better palette.
- Every theme color must be a 6-digit hex string like "#2563EB".

SlideDraft schema:
{
  "slide_draft": {
    "title": "string",
    "subtitle": "string",
    "theme": {"accent": "#2563EB", "accent2": "#0F766E", "background": "#F8FAFC", "surface": "#FFFFFF", "text": "#0F172A", "muted": "#64748B", "success": "#16A34A", "warning": "#F97316"},
    "slides": [
      {"id": "cover", "layout": "cover", "title": "string", "content": {"subtitle": "string", "kicker": "string"}, "speaker_notes": "string"},
      {"id": "section", "layout": "section_divider", "title": "string", "content": {"points": ["string"]}, "speaker_notes": "string"},
      {"id": "problems", "layout": "problem_cards", "title": "string", "content": {"problems": [{"title": "string", "body": "string"}]}, "speaker_notes": "string"},
      {"id": "metrics", "layout": "metric_cards", "title": "string", "content": {"metrics": [{"label": "string", "value": "string", "note": "string"}]}, "speaker_notes": "string"},
      {"id": "flow", "layout": "three_stage_flow", "title": "string", "content": {"steps": [{"title": "string", "body": "string"}]}, "speaker_notes": "string"},
      {"id": "timeline", "layout": "timeline", "title": "string", "content": {"events": [{"date": "string", "title": "string", "body": "string"}]}, "speaker_notes": "string"},
      {"id": "risks", "layout": "risk_table", "title": "string", "content": {"columns": ["Risk", "Impact", "Mitigation"], "rows": [["string", "string", "string"]]}, "speaker_notes": "string"},
      {"id": "compare", "layout": "comparison_table", "title": "string", "content": {"columns": ["Dimension", "Before", "After"], "rows": [["string", "string", "string"]]}, "speaker_notes": "string"},
      {"id": "next", "layout": "summary_next_steps", "title": "string", "content": {"outcomes": ["string"], "next_steps": ["string"]}, "speaker_notes": "string"}
    ]
  },
  "warnings": []
}

Use exactly the content field names shown. Because structured output schema is
strict, include all content keys on every slide; set fields that are not used by
that slide layout to "" or [] as appropriate. Do not use aliases such as items,
phases, roadmap, cards, bullets, headers, or milestones in SlideDraft.
"""

SLIDE_LAYOUTS = {
    "cover",
    "section_divider",
    "problem_cards",
    "metric_cards",
    "three_stage_flow",
    "timeline",
    "risk_table",
    "comparison_table",
    "summary_next_steps",
}

CONTENT_IR_RESPONSE_SCHEMA = {
    "name": "planb_content_ir_response",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["content_ir", "warnings"],
        "properties": {
            "warnings": {"type": "array", "items": {"type": "string"}},
            "content_ir": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "title",
                    "subtitle",
                    "audience",
                    "background",
                    "problems",
                    "metrics",
                    "solution_modules",
                    "process",
                    "implementation_plan",
                    "risks",
                    "expected_outcomes",
                    "decision_points",
                ],
                "properties": {
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "audience": {"type": "string"},
                    "background": {"type": "string"},
                    "problems": {"type": "array", "items": {"$ref": "#/$defs/card"}},
                    "metrics": {"type": "array", "items": {"$ref": "#/$defs/metric"}},
                    "solution_modules": {"type": "array", "items": {"$ref": "#/$defs/card"}},
                    "process": {"type": "array", "items": {"$ref": "#/$defs/card"}},
                    "implementation_plan": {"type": "array", "items": {"$ref": "#/$defs/event"}},
                    "risks": {"type": "array", "items": {"$ref": "#/$defs/risk"}},
                    "expected_outcomes": {"type": "array", "items": {"type": "string"}},
                    "decision_points": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "$defs": {
            "card": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "description"],
                "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
            },
            "metric": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "value", "note"],
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
            "event": {
                "type": "object",
                "additionalProperties": False,
                "required": ["date", "title", "description"],
                "properties": {
                    "date": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
            "risk": {
                "type": "object",
                "additionalProperties": False,
                "required": ["risk", "impact", "mitigation"],
                "properties": {
                    "risk": {"type": "string"},
                    "impact": {"type": "string"},
                    "mitigation": {"type": "string"},
                },
            },
        },
    },
}

SLIDE_DRAFT_RESPONSE_SCHEMA = {
    "name": "planb_slide_draft_response",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["slide_draft", "warnings"],
        "properties": {
            "warnings": {"type": "array", "items": {"type": "string"}},
            "slide_draft": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "subtitle", "theme", "slides"],
                "properties": {
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "theme": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["accent", "accent2", "background", "surface", "text", "muted", "success", "warning"],
                        "properties": {
                            "accent": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                            "accent2": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                            "background": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                            "surface": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                            "text": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                            "muted": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                            "success": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                            "warning": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                        },
                    },
                    "slides": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "anyOf": [
                                {"$ref": "#/$defs/cover_slide"},
                                {"$ref": "#/$defs/section_slide"},
                                {"$ref": "#/$defs/problem_slide"},
                                {"$ref": "#/$defs/metric_slide"},
                                {"$ref": "#/$defs/flow_slide"},
                                {"$ref": "#/$defs/timeline_slide"},
                                {"$ref": "#/$defs/risk_table_slide"},
                                {"$ref": "#/$defs/comparison_table_slide"},
                                {"$ref": "#/$defs/summary_slide"},
                            ]
                        },
                    },
                },
            },
        },
        "$defs": {
            "base_text": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "title", "speaker_notes"],
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "speaker_notes": {"type": "string"},
                },
            },
            "card": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "body"],
                "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
            },
            "metric": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "value", "note"],
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
            "event": {
                "type": "object",
                "additionalProperties": False,
                "required": ["date", "title", "body"],
                "properties": {
                    "date": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
            },
            "cover_slide": {
                "allOf": [
                    {"$ref": "#/$defs/base_text"},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["layout", "content"],
                        "properties": {
                            "layout": {"const": "cover"},
                            "content": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["subtitle", "kicker"],
                                "properties": {"subtitle": {"type": "string"}, "kicker": {"type": "string"}},
                            },
                        },
                    },
                ]
            },
            "section_slide": {
                "allOf": [
                    {"$ref": "#/$defs/base_text"},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["layout", "content"],
                        "properties": {
                            "layout": {"const": "section_divider"},
                            "content": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["points"],
                                "properties": {"points": {"type": "array", "items": {"type": "string"}}},
                            },
                        },
                    },
                ]
            },
            "problem_slide": {
                "allOf": [
                    {"$ref": "#/$defs/base_text"},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["layout", "content"],
                        "properties": {
                            "layout": {"const": "problem_cards"},
                            "content": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["problems"],
                                "properties": {"problems": {"type": "array", "items": {"$ref": "#/$defs/card"}}},
                            },
                        },
                    },
                ]
            },
            "metric_slide": {
                "allOf": [
                    {"$ref": "#/$defs/base_text"},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["layout", "content"],
                        "properties": {
                            "layout": {"const": "metric_cards"},
                            "content": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["metrics"],
                                "properties": {"metrics": {"type": "array", "items": {"$ref": "#/$defs/metric"}}},
                            },
                        },
                    },
                ]
            },
            "flow_slide": {
                "allOf": [
                    {"$ref": "#/$defs/base_text"},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["layout", "content"],
                        "properties": {
                            "layout": {"const": "three_stage_flow"},
                            "content": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["steps"],
                                "properties": {"steps": {"type": "array", "items": {"$ref": "#/$defs/card"}}},
                            },
                        },
                    },
                ]
            },
            "timeline_slide": {
                "allOf": [
                    {"$ref": "#/$defs/base_text"},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["layout", "content"],
                        "properties": {
                            "layout": {"const": "timeline"},
                            "content": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["events"],
                                "properties": {"events": {"type": "array", "items": {"$ref": "#/$defs/event"}}},
                            },
                        },
                    },
                ]
            },
            "risk_table_slide": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "title", "layout", "content", "speaker_notes"],
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "layout": {"const": "risk_table"},
                    "speaker_notes": {"type": "string"},
                    "content": {"$ref": "#/$defs/table_content"},
                },
            },
            "comparison_table_slide": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "title", "layout", "content", "speaker_notes"],
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "layout": {"const": "comparison_table"},
                    "speaker_notes": {"type": "string"},
                    "content": {"$ref": "#/$defs/table_content"},
                },
            },
            "table_content": {
                "type": "object",
                "additionalProperties": False,
                "required": ["columns", "rows"],
                "properties": {
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                },
            },
            "summary_slide": {
                "allOf": [
                    {"$ref": "#/$defs/base_text"},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["layout", "content"],
                        "properties": {
                            "layout": {"const": "summary_next_steps"},
                            "content": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["outcomes", "next_steps"],
                                "properties": {
                                    "outcomes": {"type": "array", "items": {"type": "string"}},
                                    "next_steps": {"type": "array", "items": {"type": "string"}},
                                },
                            },
                        },
                    },
                ]
            },
        },
    },
}

# OpenAI structured outputs use a constrained JSON Schema subset. Keep the
# schema flat here instead of using allOf composition so providers do not
# disagree on additionalProperties semantics. Layout-specific required content
# is still enforced after generation by _validate_slide_draft.
SLIDE_DRAFT_RESPONSE_SCHEMA = {
    "name": "planb_slide_draft_response",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["slide_draft", "warnings"],
        "properties": {
            "warnings": {"type": "array", "items": {"type": "string"}},
            "slide_draft": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "subtitle", "theme", "slides"],
                "properties": {
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "theme": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["accent", "accent2", "background", "surface", "text", "muted", "success", "warning"],
                        "properties": {
                            "accent": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                            "accent2": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                            "background": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                            "surface": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                            "text": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                            "muted": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                            "success": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                            "warning": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                        },
                    },
                    "slides": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["id", "title", "layout", "content", "speaker_notes"],
                            "properties": {
                                "id": {"type": "string"},
                                "title": {"type": "string"},
                                "layout": {
                                    "type": "string",
                                    "enum": [
                                        "cover",
                                        "section_divider",
                                        "problem_cards",
                                        "metric_cards",
                                        "three_stage_flow",
                                        "timeline",
                                        "risk_table",
                                        "comparison_table",
                                        "summary_next_steps",
                                    ],
                                },
                                "speaker_notes": {"type": "string"},
                                "content": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "subtitle",
                                        "kicker",
                                        "points",
                                        "problems",
                                        "metrics",
                                        "steps",
                                        "events",
                                        "columns",
                                        "rows",
                                        "outcomes",
                                        "next_steps",
                                    ],
                                    "properties": {
                                        "subtitle": {"type": "string"},
                                        "kicker": {"type": "string"},
                                        "points": {"type": "array", "items": {"type": "string"}},
                                        "problems": {"type": "array", "items": {"$ref": "#/$defs/card"}},
                                        "metrics": {"type": "array", "items": {"$ref": "#/$defs/metric"}},
                                        "steps": {"type": "array", "items": {"$ref": "#/$defs/card"}},
                                        "events": {"type": "array", "items": {"$ref": "#/$defs/event"}},
                                        "columns": {"type": "array", "items": {"type": "string"}},
                                        "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                                        "outcomes": {"type": "array", "items": {"type": "string"}},
                                        "next_steps": {"type": "array", "items": {"type": "string"}},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
        "$defs": {
            "card": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "body"],
                "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
            },
            "metric": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "value", "note"],
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
            "event": {
                "type": "object",
                "additionalProperties": False,
                "required": ["date", "title", "body"],
                "properties": {
                    "date": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
            },
        },
    },
}


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


def generate_content_ir_from_messages(request: dict) -> dict:
    started_at = time.perf_counter()
    normalized = normalize_agent_ir_request(request)
    warnings = list(normalized.get("warnings") or [])
    if normalized.get("ok") is False:
        return _finish("normalize_request", normalized, started_at)

    payload = normalized["request"]
    llm_result = _call_llm_json(payload, CONTENT_IR_PROMPT, CONTENT_IR_RESPONSE_SCHEMA)
    warnings.extend(llm_result.get("warnings") or [])
    if llm_result.get("ok") is not True:
        return _finish("generate_content_ir", llm_result, started_at)

    content_ir = _extract_named_object(llm_result.get("data") or {}, "content_ir")
    content_ir = _ensure_content_ir_defaults(content_ir, payload)
    validation = _validate_content_ir(content_ir)
    if validation:
        return _finish(
            "generate_content_ir",
            _error("CONTENT_IR_INVALID", "LLM returned invalid ContentIR.", validation, warnings),
            started_at,
            debug=llm_result.get("debug"),
        )

    return _finish(
        "done",
        {
            "ok": True,
            "content_ir": content_ir,
            "warnings": _dedupe(warnings),
            "debug": llm_result.get("debug") or {},
        },
        started_at,
    )


def generate_slide_draft_from_content_ir(content_ir: dict, options: dict | None = None) -> dict:
    started_at = time.perf_counter()
    payload = {"content_ir": content_ir if isinstance(content_ir, dict) else {}, "options": options or {}}
    llm_result = _call_llm_json(payload, SLIDE_DRAFT_PROMPT, SLIDE_DRAFT_RESPONSE_SCHEMA)
    warnings = list(llm_result.get("warnings") or [])
    if llm_result.get("ok") is not True:
        return _finish("generate_slide_draft", llm_result, started_at)

    slide_draft = _extract_named_object(llm_result.get("data") or {}, "slide_draft")
    slide_draft = _ensure_slide_draft_defaults(slide_draft, payload["content_ir"])
    validation = _validate_slide_draft(slide_draft)
    if validation:
        return _finish(
            "generate_slide_draft",
            _error("SLIDE_DRAFT_INVALID", "LLM returned invalid SlideDraft.", validation, warnings),
            started_at,
            debug=llm_result.get("debug"),
        )

    return _finish(
        "done",
        {
            "ok": True,
            "slide_draft": slide_draft,
            "warnings": _dedupe(warnings),
            "debug": llm_result.get("debug") or {},
        },
        started_at,
    )


def _call_llm_for_ir(payload: JsonDict) -> JsonDict:
    return _call_llm_json(payload, PLANB_IR_PROMPT)


def _call_llm_json(payload: JsonDict, system_prompt: str, response_schema: dict | None = None) -> JsonDict:
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

    request_body = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
        "response_format": _response_format(response_schema),
    }

    try:
        response = httpx.post(
            f"{settings.base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            json=request_body,
            timeout=settings.timeout_seconds,
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        if response_schema:
            fallback = _call_llm_json(payload, system_prompt, None)
            if fallback.get("ok") is True:
                fallback["warnings"] = [
                    *fallback.get("warnings", []),
                    "LLM json_schema response_format failed; retried with json_object.",
                ]
                fallback["debug"] = {
                    **_as_dict(fallback.get("debug")),
                    "json_schema_error": str(exc),
                }
                return fallback
        return _error(
            "AGENT_LLM_CALL_FAILED",
            "Agent LLM call failed.",
            str(exc),
            debug={**debug, "llm_elapsed_ms": _elapsed_ms(started_at)},
        )

    try:
        body = response.json()
        message = body["choices"][0]["message"]
        content = str(message.get("content") or "")
        if not content.strip():
            return _error(
                "AGENT_LLM_EMPTY_CONTENT",
                "Agent LLM returned an empty message.content.",
                {
                    "message_keys": sorted(message.keys()),
                    "finish_reason": (body.get("choices") or [{}])[0].get("finish_reason"),
                    "response_preview": _safe_json_preview(body),
                },
                debug={**debug, "llm_elapsed_ms": _elapsed_ms(started_at)},
            )
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
            {"content_preview": content[:1200], "content_length": len(content)},
            debug={**debug, "llm_elapsed_ms": _elapsed_ms(started_at)},
        )
    return {
        "ok": True,
        "data": data,
        "warnings": _as_list(data.get("warnings")),
        "debug": {**debug, "llm_elapsed_ms": _elapsed_ms(started_at)},
    }


def _response_format(response_schema: dict | None) -> dict:
    if not response_schema:
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": str(response_schema["name"]),
            "strict": True,
            "schema": response_schema["schema"],
        },
    }


def _safe_json_preview(value: Any, limit: int = 2000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except Exception:
        text = str(value)
    text = re.sub(r"sk-[A-Za-z0-9_\-]+", "sk-***", text)
    return text[:limit]


def _extract_ir(data: JsonDict) -> JsonDict:
    if isinstance(data.get("ir"), dict):
        return data["ir"]
    if data.get("schemaVersion") == IR_SCHEMA_VERSION:
        return data
    return {}


def _extract_named_object(data: JsonDict, key: str) -> JsonDict:
    if isinstance(data.get(key), dict):
        return data[key]
    return data if isinstance(data, dict) else {}


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
    title = str(meta.get("title") or "").strip()
    if not title:
        title = _infer_title_from_request(request)
    meta["title"] = title
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


def _ensure_content_ir_defaults(content_ir: JsonDict, request: JsonDict) -> JsonDict:
    out = json.loads(json.dumps(content_ir if isinstance(content_ir, dict) else {}, ensure_ascii=False))
    task = _as_dict(request.get("task"))
    messages = _as_list(request.get("messages"))
    title = str(out.get("title") or task.get("title") or task.get("goal") or "").strip()
    if not title:
        title = _infer_title_from_request(request)
    out["title"] = title
    out.setdefault("subtitle", str(task.get("goal") or "").strip())
    out.setdefault("audience", str(task.get("audience") or "").strip())
    out.setdefault("background", _first_message_text(messages))
    for key in (
        "problems",
        "metrics",
        "solution_modules",
        "process",
        "implementation_plan",
        "risks",
        "expected_outcomes",
        "decision_points",
    ):
        if not isinstance(out.get(key), list):
            out[key] = []
    return out


def _ensure_slide_draft_defaults(slide_draft: JsonDict, content_ir: JsonDict) -> JsonDict:
    out = json.loads(json.dumps(slide_draft if isinstance(slide_draft, dict) else {}, ensure_ascii=False))
    out.setdefault("title", str(content_ir.get("title") or "Solution Plan"))
    out.setdefault("subtitle", str(content_ir.get("subtitle") or ""))
    theme = out.setdefault("theme", {})
    if not isinstance(theme, dict):
        theme = {}
        out["theme"] = theme
    for key, value in _default_theme().items():
        theme.setdefault(key, value)
    slides = out.get("slides")
    if not isinstance(slides, list):
        out["slides"] = []
        slides = out["slides"]
    for index, slide in enumerate(slides):
        if not isinstance(slide, dict):
            continue
        slide.setdefault("id", f"slide_{index + 1}")
        slide.setdefault("title", str(slide.get("id") or f"Slide {index + 1}"))
        slide.setdefault("layout", "summary_next_steps")
        if not isinstance(slide.get("content"), dict):
            slide["content"] = {}
        slide["content"] = _normalize_slide_content(str(slide.get("layout") or ""), slide["content"])
        slide.setdefault("speaker_notes", "")
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


def _validate_content_ir(content_ir: JsonDict) -> list[str]:
    errors: list[str] = []
    if not isinstance(content_ir, dict):
        return ["ContentIR must be an object."]
    for key in ("title", "background"):
        if not str(content_ir.get(key) or "").strip():
            errors.append(f"content_ir.{key} is required.")
    for key in (
        "problems",
        "metrics",
        "solution_modules",
        "process",
        "implementation_plan",
        "risks",
        "expected_outcomes",
        "decision_points",
    ):
        if not isinstance(content_ir.get(key), list):
            errors.append(f"content_ir.{key} must be an array.")
    return errors


def _validate_slide_draft(slide_draft: JsonDict) -> list[str]:
    errors: list[str] = []
    if not isinstance(slide_draft, dict):
        return ["SlideDraft must be an object."]
    if not str(slide_draft.get("title") or "").strip():
        errors.append("slide_draft.title is required.")
    slides = slide_draft.get("slides")
    if not isinstance(slides, list) or not slides:
        errors.append("slide_draft.slides must be a non-empty array.")
        return errors
    layouts: list[str] = []
    for index, slide in enumerate(slides):
        if not isinstance(slide, dict):
            errors.append(f"slides[{index}] must be an object.")
            continue
        layout = str(slide.get("layout") or "")
        layouts.append(layout)
        for key in ("id", "title", "layout"):
            if not str(slide.get(key) or "").strip():
                errors.append(f"slides[{index}].{key} is required.")
        if layout not in SLIDE_LAYOUTS:
            errors.append(f"Unsupported slide layout: {layout}.")
        content = slide.get("content")
        if not isinstance(content, dict) or not content:
            errors.append(f"slides[{index}].content must be a non-empty object.")
        elif not _slide_content_has_renderable_items(layout, content):
            errors.append(f"slides[{index}].content does not match layout schema for {layout}.")
    for index in range(2, len(layouts)):
        if layouts[index] == layouts[index - 1] == layouts[index - 2]:
            errors.append(f"slides[{index - 2}:{index + 1}] reuse layout {layouts[index]} 3 times.")
    return errors


def _normalize_slide_content(layout: str, content: JsonDict) -> JsonDict:
    out = dict(content)
    if layout == "section_divider":
        out["points"] = _string_list_from_any(out.get("points") or out.get("bullets") or out.get("items"))
    elif layout == "problem_cards":
        out["problems"] = _card_list_from_any(out.get("problems") or out.get("cards") or out.get("items"))
    elif layout == "metric_cards":
        out["metrics"] = _metric_list_from_any(out.get("metrics") or out.get("items") or out.get("cards"))
    elif layout == "three_stage_flow":
        out["steps"] = _card_list_from_any(
            out.get("steps") or out.get("nodes") or out.get("phases") or out.get("items") or out.get("roadmap")
        )
    elif layout == "timeline":
        out["events"] = _event_list_from_any(out.get("events") or out.get("items") or out.get("milestones") or out.get("roadmap"))
    elif layout in {"risk_table", "comparison_table"}:
        out["columns"] = _string_list_from_any(out.get("columns") or out.get("headers"))
        rows = out.get("rows") or out.get("risks") or out.get("comparisons") or out.get("items")
        out["rows"] = _rows_from_any(rows)
    elif layout == "summary_next_steps":
        out["outcomes"] = _string_list_from_any(out.get("outcomes") or out.get("expected_outcomes") or out.get("benefits"))
        out["next_steps"] = _string_list_from_any(out.get("next_steps") or out.get("points") or out.get("bullets") or out.get("items"))
    return out


def _slide_content_has_renderable_items(layout: str, content: JsonDict) -> bool:
    if layout == "cover":
        return bool(str(content.get("subtitle") or content.get("kicker") or "").strip())
    if layout == "section_divider":
        return True
    if layout == "problem_cards":
        return bool(content.get("problems"))
    if layout == "metric_cards":
        return bool(content.get("metrics"))
    if layout == "three_stage_flow":
        return bool(content.get("steps"))
    if layout == "timeline":
        return bool(content.get("events"))
    if layout in {"risk_table", "comparison_table"}:
        return bool(content.get("columns")) and bool(content.get("rows"))
    if layout == "summary_next_steps":
        return bool(content.get("outcomes") or content.get("next_steps"))
    return True


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


def _infer_title_from_request(request: JsonDict) -> str:
    task = _as_dict(request.get("task"))
    for value in (task.get("title"), task.get("goal")):
        text = str(value or "").strip()
        if text:
            return text[:80]
    messages = _as_list(request.get("messages"))
    joined = " ".join(str(message.get("text") or "") for message in messages if isinstance(message, dict))
    if "onboarding" in joined.lower():
        return "公司软件 onboarding 优化方案"
    return "PlanB 工业 IR"


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


def _first_message_text(messages: list[Any]) -> str:
    for message in messages:
        if isinstance(message, dict) and str(message.get("text") or "").strip():
            return str(message.get("text")).strip()[:240]
    return "Generated from scoped messages."


def _string_list_from_any(value: Any) -> list[str]:
    if isinstance(value, list):
        output = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("title") or item.get("label") or item.get("body") or item.get("description") or item.get("text")
                if item.get("value"):
                    text = f"{text}: {item.get('value')}" if text else item.get("value")
                output.append(str(text or ""))
            else:
                output.append(str(item))
        return [item for item in output if item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _card_list_from_any(value: Any) -> list[JsonDict]:
    output = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            output.append(
                {
                    "title": str(item.get("title") or item.get("label") or item.get("name") or item.get("id") or ""),
                    "body": str(item.get("body") or item.get("description") or item.get("text") or item.get("note") or ""),
                }
            )
        elif str(item).strip():
            output.append({"title": str(item).strip(), "body": ""})
    return [item for item in output if item["title"] or item["body"]]


def _metric_list_from_any(value: Any) -> list[JsonDict]:
    output = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            output.append(
                {
                    "label": str(item.get("label") or item.get("name") or item.get("title") or ""),
                    "value": str(item.get("value") or ""),
                    "note": str(item.get("note") or item.get("target") or item.get("definition") or item.get("description") or ""),
                }
            )
    return [item for item in output if item["label"] or item["value"]]


def _event_list_from_any(value: Any) -> list[JsonDict]:
    output = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            output.append(
                {
                    "date": str(item.get("date") or item.get("time") or item.get("phase") or ""),
                    "title": str(item.get("title") or item.get("label") or item.get("name") or ""),
                    "body": str(item.get("body") or item.get("description") or item.get("text") or item.get("note") or ""),
                }
            )
    return [item for item in output if item["date"] or item["title"] or item["body"]]


def _rows_from_any(value: Any) -> list[list[str]]:
    output = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, list):
            output.append([str(cell) for cell in item])
        elif isinstance(item, dict):
            output.append([str(cell) for cell in item.values()])
        elif str(item).strip():
            output.append([str(item).strip()])
    return [row for row in output if any(cell.strip() for cell in row)]


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


__all__ = [
    "generate_content_ir_from_messages",
    "generate_ir_from_messages",
    "generate_slide_draft_from_content_ir",
]
