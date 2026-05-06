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
For summary_from_chat tasks, focus on the chat's main goals and the proposed
deliverable solution: clarify objectives, decisions, blockers, action plan,
owners when available, risks, and expected outcomes.

Rules:
- Do not produce topic summaries, task summaries, JSON Patch, Feishu OpenAPI
  payloads, files, or Markdown.
- schemaVersion must be exactly "0.2.0".
- blocks must be non-empty and use only these kinds: cover, split, flow,
  metrics, cards, table, timeline, image.
- Return {"ir": {...}, "warnings": ["..."]}.
- Every block must include all schema keys. For unused fields, return "" or [].
- Do not create empty content blocks: split.points, flow.nodes, metrics.items,
  cards.cards, table.rows, timeline.events, or image.caption/image must contain
  useful content when that kind is used.
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
- Supported layouts: cover, split, flow, metrics, cards, table, timeline,
  summary. Existing aliases section_divider, problem_cards, metric_cards,
  three_stage_flow, risk_table, comparison_table, summary_next_steps are also
  accepted.
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
- Choose theme.cover_bg as a dark cover background, theme.background as a light
  normal slide background, theme.surface as card/table background, and
  theme.accent / theme.accent2 as decorative and emphasis colors.
- Choose readable font colors: cover_title should be "#FFFFFF",
  cover_subtitle "#E2E8F0", cover_muted "#CBD5E1"; body_title/body_text should
  be dark readable colors on theme.background, body_muted a readable muted gray.
- Do not use dark body_text on dark cover_bg. Do not use white body_text on
  light normal pages.
- Do not encode placeholder text such as "click to add body" or "点击可添加正文".
- You may include slide.asset_key to request an available SVG/bitmap asset and
  slide.visual for high-level intent only, for example {"tone":"executive",
  "density":"balanced", "imagePlacement":"right", "highlightIndex":1}. Do not
  output renderer coordinates, XML, or pptxgenjs option names.

SlideDraft schema:
{
  "slide_draft": {
    "title": "string",
    "subtitle": "string",
    "theme": {"accent": "#2563EB", "accent2": "#0F766E", "background": "#F8FAFC", "surface": "#FFFFFF", "text": "#0F172A", "muted": "#64748B", "success": "#16A34A", "warning": "#F97316", "cover_bg": "#0F172A", "cover_title": "#FFFFFF", "cover_subtitle": "#E2E8F0", "cover_muted": "#CBD5E1", "body_title": "#0F172A", "body_text": "#0F172A", "body_muted": "#64748B"},
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
    "split",
    "flow",
    "metrics",
    "cards",
    "table",
    "summary",
    "section_divider",
    "problem_cards",
    "metric_cards",
    "three_stage_flow",
    "timeline",
    "risk_table",
    "comparison_table",
    "summary_next_steps",
}

SLIDE_LAYOUT_ALIASES = {
    "split": "section_divider",
    "cards": "problem_cards",
    "metrics": "metric_cards",
    "flow": "three_stage_flow",
    "table": "risk_table",
    "summary": "summary_next_steps",
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

ARTIFACT_IR_RESPONSE_SCHEMA = {
    "name": "planb_artifact_ir_response",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["ir", "warnings"],
        "properties": {
            "warnings": {"type": "array", "items": {"type": "string"}},
            "ir": {
                "type": "object",
                "additionalProperties": False,
                "required": ["schemaVersion", "docId", "meta", "theme", "assets", "blocks"],
                "properties": {
                    "schemaVersion": {"type": "string", "enum": [IR_SCHEMA_VERSION]},
                    "docId": {"type": "string"},
                    "meta": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["title", "subtitle", "owner", "date", "audience"],
                        "properties": {
                            "title": {"type": "string"},
                            "subtitle": {"type": "string"},
                            "owner": {"type": "string"},
                            "date": {"type": "string"},
                            "audience": {"type": "string"},
                        },
                    },
                    "theme": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "name",
                            "accent",
                            "accent2",
                            "background",
                            "surface",
                            "text",
                            "muted",
                            "success",
                            "warning",
                            "fontFace",
                        ],
                        "properties": {
                            "name": {"type": "string"},
                            "accent": {"type": "string"},
                            "accent2": {"type": "string"},
                            "background": {"type": "string"},
                            "surface": {"type": "string"},
                            "text": {"type": "string"},
                            "muted": {"type": "string"},
                            "success": {"type": "string"},
                            "warning": {"type": "string"},
                            "fontFace": {"type": "string"},
                        },
                    },
                    "assets": {"type": "object", "additionalProperties": False, "properties": {}},
                    "blocks": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "id",
                                "kind",
                                "title",
                                "subtitle",
                                "kicker",
                                "points",
                                "nodes",
                                "edges",
                                "items",
                                "cards",
                                "columns",
                                "rows",
                                "events",
                                "caption",
                                "image",
                            ],
                            "properties": {
                                "id": {"type": "string"},
                                "kind": {
                                    "type": "string",
                                    "enum": ["cover", "split", "flow", "metrics", "cards", "table", "timeline", "image"],
                                },
                                "title": {"type": "string"},
                                "subtitle": {"type": "string"},
                                "kicker": {"type": "string"},
                                "points": {"type": "array", "items": {"type": "string"}},
                                "nodes": {"type": "array", "items": {"$ref": "#/$defs/node"}},
                                "edges": {"type": "array", "items": {"$ref": "#/$defs/edge"}},
                                "items": {"type": "array", "items": {"$ref": "#/$defs/metric"}},
                                "cards": {"type": "array", "items": {"$ref": "#/$defs/card_body"}},
                                "columns": {"type": "array", "items": {"type": "string"}},
                                "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                                "events": {"type": "array", "items": {"$ref": "#/$defs/event_body"}},
                                "caption": {"type": "string"},
                                "image": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
        "$defs": {
            "node": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "label"],
                "properties": {"id": {"type": "string"}, "label": {"type": "string"}},
            },
            "edge": {
                "type": "object",
                "additionalProperties": False,
                "required": ["from", "to"],
                "properties": {"from": {"type": "string"}, "to": {"type": "string"}},
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
            "card_body": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "body"],
                "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
            },
            "event_body": {
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

SLIDE_CONTENT_PROPERTIES = {
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
}


THEME_COLOR_FIELDS = [
    "accent",
    "accent2",
    "background",
    "surface",
    "text",
    "muted",
    "success",
    "warning",
    "cover_bg",
    "cover_title",
    "cover_subtitle",
    "cover_muted",
    "body_title",
    "body_text",
    "body_muted",
]


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
                        "required": THEME_COLOR_FIELDS,
                        "properties": {
                            field: {"type": "string"}
                            for field in THEME_COLOR_FIELDS
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
                                        "split",
                                        "flow",
                                        "metrics",
                                        "cards",
                                        "table",
                                        "summary",
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
                                "asset_key": {"type": "string"},
                                "visual": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["tone", "density", "imagePlacement", "highlightIndex"],
                                    "properties": {
                                        "tone": {"type": "string"},
                                        "density": {"type": "string"},
                                        "imagePlacement": {"type": "string"},
                                        "highlightIndex": {"type": "integer"},
                                    },
                                },
                                "speaker_notes": {"type": "string"},
                                "content": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": list(SLIDE_CONTENT_PROPERTIES.keys()),
                                    "properties": SLIDE_CONTENT_PROPERTIES,
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
    ir = _coerce_artifact_ir(ir)
    ir = _ensure_ir_defaults(ir, payload)
    validation = _validate_ir(ir)
    if validation:
        fallback_ir = _summary_ir_from_request(payload)
        fallback_validation = _validate_ir(fallback_ir)
        if not fallback_validation:
            return _finish(
                "done",
                {
                    "ok": True,
                    "ir": fallback_ir,
                    "warnings": _dedupe(
                        [
                            *warnings,
                            "LLM returned invalid IR; generated a conservative summary IR from scoped messages.",
                        ]
                    ),
                    "debug": {
                        **_as_dict(llm_result.get("debug")),
                        "llm_ir_validation": validation,
                        "fallback_ir": True,
                    },
                },
                started_at,
            )
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
    llm_result = _call_llm_json(
        payload,
        CONTENT_IR_PROMPT,
        CONTENT_IR_RESPONSE_SCHEMA,
        model_override=_ppt_model_for_payload(payload),
        purpose="content_ir",
    )
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
    llm_result = _call_llm_json(
        payload,
        SLIDE_DRAFT_PROMPT,
        SLIDE_DRAFT_RESPONSE_SCHEMA,
        model_override=_ppt_model_from_options(options),
        purpose="slide_draft",
    )
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
    options = _as_dict(payload.get("options"))
    model_override = str(options.get("llm_model") or options.get("model") or "").strip() or None
    return _call_llm_json(
        payload,
        PLANB_IR_PROMPT,
        ARTIFACT_IR_RESPONSE_SCHEMA,
        model_override=model_override,
        purpose="artifact_ir",
    )


def _call_llm_json(
    payload: JsonDict,
    system_prompt: str,
    response_schema: dict | None = None,
    *,
    model_override: str | None = None,
    purpose: str = "agent",
) -> JsonDict:
    settings = get_agent_settings()
    model = str(model_override or settings.model).strip()
    started_at = time.perf_counter()
    debug = {
        "llm_provider": settings.provider,
        "llm_model": model,
        "llm_purpose": purpose,
        "llm_reasoning_effort": settings.reasoning_effort,
    }
    missing = [
        name
        for name, value in (
            ("AGENT_LLM_BASE_URL", settings.base_url),
            ("AGENT_LLM_API_KEY", settings.api_key),
            ("AGENT_LLM_MODEL", model),
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
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "response_format": _response_format(response_schema),
    }
    request_body[_token_limit_param(model)] = settings.max_tokens
    if _supports_reasoning_effort(model) and settings.reasoning_effort:
        request_body["reasoning_effort"] = settings.reasoning_effort
    if _supports_temperature(model):
        request_body["temperature"] = settings.temperature

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
        details = _httpx_exception_details(exc)
        if response_schema:
            fallback = _call_llm_json(
                payload,
                system_prompt,
                None,
                model_override=model,
                purpose=purpose,
            )
            if fallback.get("ok") is True:
                fallback["warnings"] = [
                    *fallback.get("warnings", []),
                    "LLM json_schema response_format failed; retried with json_object.",
                ]
                fallback["debug"] = {
                    **_as_dict(fallback.get("debug")),
                    "json_schema_error": details,
                }
                return fallback
        return _error(
            "AGENT_LLM_CALL_FAILED",
            "Agent LLM call failed.",
            details,
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


def _ppt_model_for_payload(payload: JsonDict) -> str | None:
    options = _as_dict(payload.get("options"))
    targets = options.get("target_outputs") or []
    if isinstance(targets, str):
        targets = [item.strip() for item in targets.split(",") if item.strip()]
    if isinstance(targets, list) and "ppt" in {str(target).strip() for target in targets}:
        return get_agent_settings().ppt_model
    return None


def _ppt_model_from_options(options: dict | None) -> str:
    opts = _as_dict(options)
    explicit = str(opts.get("llm_model") or opts.get("model") or "").strip()
    return explicit or get_agent_settings().ppt_model


def _token_limit_param(model: str) -> str:
    normalized = model.lower()
    if normalized.startswith("gpt-5") or normalized.startswith("o"):
        return "max_completion_tokens"
    return "max_tokens"


def _supports_temperature(model: str) -> bool:
    normalized = model.lower()
    return not (normalized.startswith("gpt-5") or normalized.startswith("o"))


def _supports_reasoning_effort(model: str) -> bool:
    normalized = model.lower()
    return normalized.startswith("gpt-5") or normalized.startswith("o")


def _httpx_exception_details(exc: Exception) -> Any:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        body = (response.text or "").strip()
        return {
            "message": str(exc),
            "status_code": response.status_code,
            "response_body": body[:2000],
        }
    return str(exc)


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


def _coerce_artifact_ir(ir: JsonDict) -> JsonDict:
    if not isinstance(ir, dict):
        return {}
    out = json.loads(json.dumps(ir, ensure_ascii=False))
    blocks = out.get("blocks")
    if not isinstance(blocks, list):
        return out
    out["blocks"] = [
        _coerce_artifact_block(block, index)
        for index, block in enumerate(blocks)
        if isinstance(block, dict)
    ]
    return out


def _coerce_artifact_block(block: JsonDict, index: int) -> JsonDict:
    out = dict(block)
    block_id = str(out.get("id") or out.get("block_id") or "").strip()
    if not block_id:
        block_id = f"block_{index + 1}"
    out["id"] = block_id

    title = str(
        out.get("title")
        or out.get("heading")
        or out.get("name")
        or out.get("label")
        or block_id
    ).strip()
    out["title"] = title or block_id

    kind = _canonical_block_kind(
        str(out.get("kind") or out.get("type") or out.get("layout") or "").strip(),
        out,
    )
    out["kind"] = kind

    content = out.get("content")
    if kind == "split":
        points = _string_list_from_any(
            out.get("points")
            or out.get("bullets")
            or out.get("items")
            or out.get("summary")
            or out.get("body")
            or out.get("description")
            or content
        )
        if points:
            out["points"] = points
    elif kind == "cards":
        cards = _card_list_from_any(
            out.get("cards")
            or out.get("items")
            or out.get("problems")
            or out.get("solutions")
            or out.get("modules")
            or content
        )
        if cards:
            out["cards"] = cards
    elif kind == "metrics":
        items = _metric_list_from_any(out.get("items") or out.get("metrics") or out.get("cards") or content)
        if items:
            out["items"] = items
    elif kind == "table":
        if not isinstance(out.get("columns"), list):
            out["columns"] = _string_list_from_any(out.get("columns") or out.get("headers"))
        if not isinstance(out.get("rows"), list):
            out["rows"] = _rows_from_any(out.get("rows") or out.get("items") or content)
    elif kind == "timeline":
        events = _event_list_from_any(out.get("events") or out.get("items") or out.get("timeline") or content)
        if events:
            out["events"] = events
    elif kind == "flow":
        if not isinstance(out.get("nodes"), list):
            out["nodes"] = _flow_nodes_from_any(out.get("nodes") or out.get("steps") or out.get("items") or content)
        if not isinstance(out.get("edges"), list):
            out["edges"] = _linear_edges_for_nodes(out.get("nodes"))
    return out


def _canonical_block_kind(value: str, block: JsonDict) -> str:
    normalized = value.lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "section": "split",
        "paragraph": "split",
        "summary": "split",
        "bullets": "split",
        "bullet": "split",
        "text": "split",
        "card": "cards",
        "metric": "metrics",
        "process": "flow",
        "steps": "flow",
        "roadmap": "timeline",
    }
    if normalized in ALLOWED_BLOCK_KINDS:
        return normalized
    if normalized in aliases:
        return aliases[normalized]
    if isinstance(block.get("nodes"), list) or isinstance(block.get("steps"), list):
        return "flow"
    if isinstance(block.get("metrics"), list):
        return "metrics"
    if isinstance(block.get("cards"), list) or isinstance(block.get("problems"), list):
        return "cards"
    if isinstance(block.get("columns"), list) or isinstance(block.get("rows"), list):
        return "table"
    if isinstance(block.get("events"), list) or isinstance(block.get("timeline"), list):
        return "timeline"
    return "split"


def _flow_nodes_from_any(value: Any) -> list[JsonDict]:
    nodes = []
    for index, item in enumerate(value if isinstance(value, list) else []):
        if isinstance(item, dict):
            node_id = str(item.get("id") or item.get("key") or f"n{index + 1}")
            label = str(item.get("label") or item.get("title") or item.get("name") or item.get("body") or node_id)
        else:
            text = str(item).strip()
            if not text:
                continue
            node_id = f"n{index + 1}"
            label = text
        nodes.append({"id": node_id, "label": label})
    return nodes


def _linear_edges_for_nodes(nodes: Any) -> list[JsonDict]:
    if not isinstance(nodes, list):
        return []
    ids = [str(node.get("id")) for node in nodes if isinstance(node, dict) and node.get("id")]
    return [{"from": ids[index], "to": ids[index + 1]} for index in range(len(ids) - 1)]


def _summary_ir_from_request(request: JsonDict) -> JsonDict:
    task = _as_dict(request.get("task"))
    messages = [message for message in _as_list(request.get("messages")) if isinstance(message, dict)]
    title = str(task.get("title") or _infer_title_from_request(request)).strip() or "群聊目标与方案总结"
    goal = str(task.get("goal") or "").strip()
    audience = str(task.get("audience") or "").strip() or "群聊成员"
    snippets = _message_snippets(messages, limit=8)
    body = "\n".join(snippets) or "本次时间窗内没有可用的文本消息。"
    action_items = _derive_action_items(snippets)
    return {
        "schemaVersion": IR_SCHEMA_VERSION,
        "docId": f"{_safe_id(str(task.get('task_id') or 'summary'))}_ir",
        "meta": {
            "title": title,
            "subtitle": goal or "基于群聊消息生成的摘要。",
            "owner": "Agent",
            "date": date.today().isoformat(),
            "audience": audience,
        },
        "theme": _default_theme(),
        "assets": {},
        "blocks": [
            {
                "id": "cover",
                "kind": "cover",
                "title": title,
                "subtitle": goal or "整理主要目标、共识、问题与下一步。",
            },
            {
                "id": "message_evidence",
                "kind": "split",
                "title": "群聊消息依据",
                "points": snippets or [body],
            },
            {
                "id": "summary_points",
                "kind": "cards",
                "title": "目标与方案要点",
                "cards": [
                    {"title": "主要目标", "body": goal or _truncate(body, 180)},
                    {"title": "当前共识", "body": "以群聊消息为依据生成可发布的飞书文档，并保留后续人工校对空间。"},
                    {"title": "待确认问题", "body": "模型输出、消息范围、负责人和具体交付标准需要结合群内上下文确认。"},
                ],
            },
            {
                "id": "next_steps",
                "kind": "timeline",
                "title": "下一步",
                "events": [
                    {"date": "现在", "title": item["title"], "body": item["body"]}
                    for item in action_items
                ],
            },
        ],
    }


def _message_snippets(messages: list[JsonDict], limit: int = 8) -> list[str]:
    snippets = []
    for message in messages:
        text = str(message.get("text") or "").strip()
        if not text:
            continue
        sender = str(message.get("sender") or message.get("sender_name") or "成员").strip()
        snippets.append(f"{sender}: {_truncate(text, 180)}")
        if len(snippets) >= limit:
            break
    return snippets


def _derive_action_items(snippets: list[str]) -> list[JsonDict]:
    if not snippets:
        return [{"title": "补充上下文", "body": "提供更完整的群聊消息后重新生成总结。"}]
    return [
        {"title": "复核目标", "body": "确认文档标题、目标、受众和交付格式是否符合群聊意图。"},
        {"title": "补充负责人", "body": "从群聊中确认行动项负责人、截止时间和验收标准。"},
        {"title": "发布文档", "body": "生成飞书文档后在群内回复链接，收集修改意见。"},
    ]


def _truncate(text: str, limit: int) -> str:
    value = str(text or "").strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


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
        slide["layout"] = _canonical_slide_layout(str(slide.get("layout") or ""))
        slide.setdefault("asset_key", "")
        if not isinstance(slide.get("visual"), dict):
            slide["visual"] = {"tone": "", "density": "", "imagePlacement": "", "highlightIndex": -1}
        else:
            visual = slide["visual"]
            visual.setdefault("tone", "")
            visual.setdefault("density", "")
            visual.setdefault("imagePlacement", "")
            visual.setdefault("highlightIndex", -1)
        if not isinstance(slide.get("content"), dict):
            slide["content"] = {}
        layout = str(slide.get("layout") or "")
        slide["content"] = _normalize_slide_content(layout, slide["content"])
        if not _slide_content_has_renderable_items(layout, slide["content"]):
            slide["content"] = _fallback_slide_content(layout, slide["content"], slide, content_ir)
        slide.setdefault("speaker_notes", "")
    return out


def _canonical_slide_layout(layout: str) -> str:
    value = str(layout or "").strip()
    return SLIDE_LAYOUT_ALIASES.get(value, value)


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
        elif not _block_has_renderable_content(block):
            errors.append(f"Block {block_id or index} has no renderable content for kind {kind}.")
    return errors


def _block_has_renderable_content(block: JsonDict) -> bool:
    kind = str(block.get("kind") or "").strip()
    if kind == "cover":
        return bool(str(block.get("subtitle") or block.get("kicker") or block.get("title") or "").strip())
    if kind == "split":
        return bool(_as_list(block.get("points")))
    if kind == "flow":
        return bool(_as_list(block.get("nodes")))
    if kind == "metrics":
        return bool(_as_list(block.get("items")))
    if kind == "cards":
        return bool(_as_list(block.get("cards")))
    if kind == "table":
        return bool(_as_list(block.get("columns")) or _as_list(block.get("rows")))
    if kind == "timeline":
        return bool(_as_list(block.get("events")))
    if kind == "image":
        return bool(str(block.get("caption") or block.get("image") or "").strip())
    return False


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
        layout = _canonical_slide_layout(str(slide.get("layout") or ""))
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
    layout = _canonical_slide_layout(layout)
    if layout == "section_divider":
        out["points"] = _string_list_from_any(
            out.get("points")
            or out.get("bullets")
            or out.get("items")
            or [out.get("left"), out.get("right"), out.get("body"), out.get("description")]
        )
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


def _fallback_slide_content(
    layout: str,
    content: JsonDict,
    slide: JsonDict,
    content_ir: JsonDict,
) -> JsonDict:
    title = str(slide.get("title") or content_ir.get("title") or "Summary").strip()
    background = str(content_ir.get("background") or content_ir.get("subtitle") or title).strip()
    out = dict(content)
    if layout == "cover":
        return {
            **out,
            "subtitle": str(content_ir.get("subtitle") or background or title),
            "kicker": str(content_ir.get("audience") or "Generated presentation"),
        }
    if layout == "problem_cards":
        cards = _card_list_from_any(
            content_ir.get("problems")
            or content_ir.get("solution_modules")
            or content_ir.get("risks")
            or [{"title": title, "description": background}]
        )
        return {**out, "problems": cards or [{"title": title, "body": background}]}
    if layout == "metric_cards":
        metrics = _metric_list_from_any(content_ir.get("metrics") or [])
        if not metrics:
            metrics = [{"label": "核心目标", "value": title[:24], "note": background[:80]}]
        return {**out, "metrics": metrics}
    if layout == "three_stage_flow":
        steps = _card_list_from_any(
            content_ir.get("process")
            or content_ir.get("solution_modules")
            or content_ir.get("implementation_plan")
            or [{"title": "下一步", "description": background}]
        )
        return {**out, "steps": steps or [{"title": "下一步", "body": background}]}
    if layout == "timeline":
        events = _event_list_from_any(content_ir.get("implementation_plan") or [])
        if not events:
            events = [{"date": "近期", "title": title, "body": background}]
        return {**out, "events": events}
    if layout in {"risk_table", "comparison_table"}:
        rows = _rows_from_any(content_ir.get("risks") or [])
        if not rows:
            rows = [[title, background]]
        columns = out.get("columns") or ["事项", "说明"]
        return {**out, "columns": columns, "rows": rows}
    if layout == "summary_next_steps":
        outcomes = _string_list_from_any(content_ir.get("expected_outcomes") or [])
        next_steps = _string_list_from_any(content_ir.get("decision_points") or content_ir.get("process") or [])
        return {
            **out,
            "outcomes": outcomes or [background or title],
            "next_steps": next_steps or [title],
        }
    return out or {"points": [background or title]}


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
        "cover_bg": "#0F172A",
        "cover_title": "#FFFFFF",
        "cover_subtitle": "#E2E8F0",
        "cover_muted": "#CBD5E1",
        "body_title": "#0F172A",
        "body_text": "#0F172A",
        "body_muted": "#64748B",
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
            if item is None:
                continue
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
