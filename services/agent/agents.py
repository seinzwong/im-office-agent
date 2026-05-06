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
For summary_from_chat tasks, treat the task as faithful meeting notes unless
the messages explicitly ask for a business proposal. Summarize only facts that
appear in messages, and do not turn casual chat into a generic objectives /
solution / risk template.

Rules:
- Do not produce topic summaries, task summaries, JSON Patch, Feishu OpenAPI
  payloads, files, or Markdown.
- schemaVersion must be exactly "0.2.0".
- blocks must be non-empty and use only these kinds: cover, split, flow,
  metrics, cards, table, timeline, image.
- Return {"ir": {...}, "warnings": ["..."]}.
- Set meta.file_name to a concise human-readable output file name without an
  extension, summarized from the content.
- Prefer readable, publishable blocks: use table for structured lists, cards
  for explanatory modules, timeline for ordered plans, and split for key
  conclusions.
- Use block.intent to describe purpose, such as summary, actions, risks,
  decisions, comparison, evidence, or metrics. Do not depend on intent for
  content; still fill the block's normal fields.
- When intent is actions, risks, comparison, or metrics, kind must be table.
  Put owner, status, due dates, risks, impacts, and evidence in columns/rows,
  not in cards.
- Every block must include all schema keys. For unused fields, return "" or [].
- For table blocks, columns must be meaningful headers and rows must contain
  actual cell text. Use "待确认" for unknown values instead of empty cells.
- Use description for one short section explanation and sourceRefs for message,
  file, or time references when available.
- For cards, put compact metadata such as owner, status, due, link, or source
  in cards[].meta. Do not put URLs, message IDs, source IDs, long prose, or
  unknown/default values such as 待确认 in meta; put references in sourceRefs.
- Do not create empty content blocks: split.points, flow.nodes, metrics.items,
  cards.cards, table.rows, timeline.events, or image.caption/image must contain
  useful content when that kind is used.

For summary_from_chat:
- Use only message-grounded facts. Do not include system implementation details
  such as OAuth, permissions, Agent, OpenAPI, document creation failures, or
  bot/runtime status unless the chat messages explicitly mention them.
- For plan-like chat, extract plan name, purpose, rules, levels, time windows,
  action items, owners, blockers, and open questions only when present in
  messages. If owner, date, status, or acceptance criteria is missing, write
  "待确认"; do not invent it.
- Map discussion/background to kind=split intent=summary with factual points.
  Map rules, levels, limits, or comparisons to kind=table with columns such as
  规则项, 内容, 适用条件, 来源. Map action items to kind=table intent=actions
  with columns such as 事项, 负责人, 时间, 状态, 验收标准. Map explicit schedules
  or time windows to kind=timeline intent=summary.
- Generate a risks block only when the chat explicitly discusses risks. Avoid
  generic risks such as missing permissions or failed document creation unless
  those words appear in messages.
- Do not output any field outside the schema. In particular, do not create
  fields named plan, schedule, rules, constraints, participants, or sources
  outside sourceRefs.
- Prefer source-grounded wording. Each non-cover block should include sourceRefs
  when possible, using speaker names, message ids, or times from the input.
- For sourceRefs in summary_from_chat, prefer exact message ids from the input
  such as message:<message_id>. Do not use generic refs like "消息" when a
  message id is available.
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
- slide_draft must contain title, file_name, subtitle, theme, and slides.
- Each slide must contain id, title, layout, content, and speaker_notes.
- Supported layouts: cover, split, flow, metrics, cards, table, timeline,
  summary. Do not output legacy layout aliases.
- Every slide content must be non-empty.
- split may use an empty points array when the slide title itself is
  the section message.
- Use split only for pure section breaks such as "Background",
  "Solution", or "Plan". Do not use split for substantive content
  such as decision points, key actions, risks, milestones, or recommendations;
  use summary, flow, table, timeline, or cards.
- Do not use the same layout for 3 consecutive slides.
- If several structured sections are needed back-to-back, alternate table with
  summary, cards, timeline, or flow. Merge small tables when possible instead
  of producing 3 consecutive table slides.
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
- Use theme.fontFace for the presentation font. Prefer "Microsoft YaHei" for
  zh-CN decks unless the user explicitly requests another available font.
- Keep slide copy short enough for PPT cards: slide titles <= 22 Chinese chars,
  card/flow titles <= 14 Chinese chars, card bodies <= 36 Chinese chars, flow
  bodies <= 24 Chinese chars, summary bullets <= 32 Chinese chars. Use tables
  instead of cards/flow when items need longer descriptions.
- Cards should contain at most 4 items. Flow should contain at most 4 steps.
  If there are 5+ comparable items or long stage descriptions, choose table,
  timeline, or summary instead.
- Do not use dark body_text on dark cover_bg. Do not use white body_text on
  light normal pages.
- Do not encode placeholder text such as "click to add body" or "点击可添加正文".
- You may include slide.asset_key to request an available SVG/bitmap asset and
  slide.visual.highlightIndex to emphasize one flow step. Do not output
  renderer coordinates, XML, or pptxgenjs option names.
- For each slide content object, include only the fields used by that layout:
  cover uses subtitle/kicker/owner/audience; split uses points; cards uses
  cards; metrics uses metrics; flow uses steps; table uses columns/rows;
  timeline uses events; summary uses next_steps/outcomes.

SlideDraft schema:
{
  "slide_draft": {
    "title": "string",
    "file_name": "string",
    "subtitle": "string",
    "theme": {"accent": "#2563EB", "accent2": "#0F766E", "background": "#F8FAFC", "surface": "#FFFFFF", "text": "#0F172A", "muted": "#64748B", "success": "#16A34A", "warning": "#F97316", "cover_bg": "#0F172A", "cover_title": "#FFFFFF", "cover_subtitle": "#E2E8F0", "cover_muted": "#CBD5E1", "body_title": "#0F172A", "body_text": "#0F172A", "body_muted": "#64748B", "fontFace": "Microsoft YaHei"},
    "slides": [
      {"id": "cover", "layout": "cover", "title": "string", "content": {"subtitle": "string", "kicker": "string"}, "speaker_notes": "string"},
      {"id": "section", "layout": "split", "title": "string", "content": {"points": ["string"]}, "speaker_notes": "string"},
      {"id": "problems", "layout": "cards", "title": "string", "content": {"cards": [{"title": "string", "body": "string"}]}, "speaker_notes": "string"},
      {"id": "metrics", "layout": "metrics", "title": "string", "content": {"metrics": [{"label": "string", "value": "string", "note": "string"}]}, "speaker_notes": "string"},
      {"id": "flow", "layout": "flow", "title": "string", "content": {"steps": [{"title": "string", "body": "string"}]}, "speaker_notes": "string"},
      {"id": "timeline", "layout": "timeline", "title": "string", "content": {"events": [{"date": "string", "title": "string", "body": "string"}]}, "speaker_notes": "string"},
      {"id": "risks", "layout": "table", "title": "string", "content": {"columns": ["Risk", "Impact", "Mitigation"], "rows": [["string", "string", "string"]]}, "speaker_notes": "string"},
      {"id": "next", "layout": "summary", "title": "string", "content": {"outcomes": ["string"], "next_steps": ["string"]}, "speaker_notes": "string"}
    ]
  },
  "warnings": []
}

Use exactly the content field names shown. Do not include fields that are not
consumed by the slide layout. Do not use aliases such as items, problems,
phases, roadmap, bullets, headers, risks, comparisons, or milestones in
SlideDraft.
"""

BOARD_IR_PROMPT = """You are the Board Agent in PlanB.

Use the provided ContentIR to produce one BoardIR for a high-quality
whiteboard. Return JSON only.

Rules:
- Return {"board_ir": {...}, "warnings": ["..."]}.
- Do not invent new business facts beyond ContentIR.
- BoardIR must contain title, file_name, subtitle, theme, and sections.
- Board sections must express solution logic clearly:
  background/problems -> goals/metrics -> main flow -> modules -> risks ->
  timeline -> next steps.
- Board is a spatial expression, not a document summary. Keep hierarchy clear.
- Use only supported section kinds:
  overview, flow, cards, metrics, table, timeline, summary.
- Include only fields consumed by each kind. Do not output unknown fields.
- Each section must contain the minimum renderable field for its kind:
  overview/cards/summary use non-empty items; flow uses non-empty nodes and
  edges; metrics uses non-empty metrics; table uses non-empty columns and rows;
  timeline uses non-empty events. Use "待确认" only when the source lacks a
  specific detail.
- Keep section text concise and scannable.
"""

SLIDE_LAYOUTS = {
    "cover",
    "split",
    "flow",
    "metrics",
    "cards",
    "table",
    "summary",
    "timeline",
}

SLIDE_LAYOUT_ALIASES: dict[str, str] = {}

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
                        "required": ["title", "file_name", "subtitle", "owner", "date", "audience"],
                        "properties": {
                            "title": {"type": "string"},
                            "file_name": {"type": "string"},
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
                                "description",
                                "intent",
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
                                "sourceRefs",
                            ],
                            "properties": {
                                "id": {"type": "string"},
                                "kind": {
                                    "type": "string",
                                    "enum": ["cover", "split", "flow", "metrics", "cards", "table", "timeline", "image"],
                                },
                                "title": {"type": "string", "description": "Section title shown as a heading."},
                                "description": {
                                    "type": "string",
                                    "description": "One short sentence shown below the heading to explain this section's context. Use an empty string if not needed.",
                                },
                                "intent": {
                                    "type": "string",
                                    "description": "Semantic purpose such as summary, actions, risks, decisions, comparison, evidence, or metrics. If intent is actions, risks, comparison, or metrics, use kind=table and put the structured data in columns/rows.",
                                },
                                "subtitle": {"type": "string"},
                                "kicker": {"type": "string"},
                                "points": {
                                    "type": "array",
                                    "description": "Complete bullet sentences for split blocks. Use for conclusions, decisions, or evidence notes.",
                                    "items": {"type": "string"},
                                },
                                "nodes": {"type": "array", "items": {"$ref": "#/$defs/node"}},
                                "edges": {"type": "array", "items": {"$ref": "#/$defs/edge"}},
                                "items": {"type": "array", "items": {"$ref": "#/$defs/metric"}},
                                "cards": {
                                    "type": "array",
                                    "description": "Cards for explanatory modules. Each card should have a specific title, readable body, and compact meta for owner/status/due/link/source when useful.",
                                    "items": {"$ref": "#/$defs/card_body"},
                                },
                                "columns": {
                                    "type": "array",
                                    "description": "Table header labels. For action lists prefer 事项, 负责人, 截止时间, 状态, 验收标准; for risks prefer 风险, 影响, 缓解措施, 负责人. Keep headers concise and non-empty.",
                                    "items": {"type": "string"},
                                },
                                "rows": {
                                    "type": "array",
                                    "description": "Actual table body cell text. Each row must align with columns. Do not use empty placeholders; use 待确认 for unknown values.",
                                    "items": {"type": "array", "items": {"type": "string"}},
                                },
                                "events": {"type": "array", "items": {"$ref": "#/$defs/event_body"}},
                                "caption": {"type": "string"},
                                "image": {"type": "string"},
                                "sourceRefs": {
                                    "type": "array",
                                    "description": "References to source messages, files, links, speakers, or times that support this block. Use empty array when unavailable.",
                                    "items": {"type": "string"},
                                },
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
                "required": ["title", "body", "meta"],
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "meta": {
                        "type": "array",
                        "description": "Compact key-value facts such as owner: Alice, status: open, due: Friday. Do not include URLs, message IDs, source IDs, long prose, or unknown/default values such as 待确认.",
                        "items": {"type": "string"},
                    },
                },
            },
            "event_body": {
                "type": "object",
                "additionalProperties": False,
                "required": ["date", "title", "body", "owner", "status"],
                "properties": {
                    "date": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "owner": {"type": "string"},
                    "status": {"type": "string"},
                },
            },
        },
    },
}

SLIDE_CONTENT_PROPERTIES = {
    "subtitle": {"type": "string", "description": "Cover subtitle. Use only on cover slides."},
    "kicker": {"type": "string", "description": "Short cover eyebrow or audience label. Use only on cover slides."},
    "owner": {"type": "string", "description": "Optional cover owner or source label. Use only on cover slides."},
    "audience": {"type": "string", "description": "Optional cover audience. Use only on cover slides."},
    "points": {"type": "array", "description": "Short bullet lines for split slides only.", "items": {"type": "string", "description": "One concise bullet."}},
    "cards": {"type": "array", "description": "Cards for cards slides only. Keep at most 4 items.", "items": {"$ref": "#/$defs/card"}},
    "metrics": {"type": "array", "description": "Metric cards for metrics slides only. Keep at most 4 items.", "items": {"$ref": "#/$defs/metric"}},
    "steps": {"type": "array", "description": "Process steps for flow slides only. Keep at most 4 steps.", "items": {"$ref": "#/$defs/card"}},
    "events": {"type": "array", "description": "Timeline events for timeline slides only. Keep at most 5 events.", "items": {"$ref": "#/$defs/event"}},
    "columns": {"type": "array", "description": "Table headers for table slides only.", "items": {"type": "string", "description": "Concise table header."}},
    "rows": {"type": "array", "description": "Table rows for table slides only; every row must match columns length.", "items": {"type": "array", "items": {"type": "string", "description": "Table cell text."}}},
    "outcomes": {"type": "array", "description": "Expected outcomes for summary slides only.", "items": {"type": "string", "description": "One concise outcome."}},
    "next_steps": {"type": "array", "description": "Next actions for summary slides only.", "items": {"type": "string", "description": "One concise next step."}},
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
    "fontFace",
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
                "description": "Renderer-ready PPT deck draft for pptxgenjs.",
                "additionalProperties": False,
                "required": ["title", "file_name", "subtitle", "theme", "slides"],
                "properties": {
                    "title": {"type": "string", "description": "Presentation title shown on the cover and document metadata."},
                    "file_name": {"type": "string", "description": "Concise human-readable output file name without extension, summarized from the content."},
                    "subtitle": {"type": "string", "description": "Presentation subtitle or one-sentence context."},
                    "theme": {
                        "type": "object",
                        "description": "Visual theme used by the pptx renderer.",
                        "additionalProperties": False,
                        "required": THEME_COLOR_FIELDS,
                        "properties": {
                            field: {"type": "string", "description": f"Theme field {field}."}
                            for field in THEME_COLOR_FIELDS
                        },
                    },
                    "slides": {
                        "type": "array",
                        "description": "Ordered deck slides.",
                        "minItems": 1,
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "description": "One slide using a renderer-supported layout.",
                            "additionalProperties": False,
                            "required": ["id", "title", "layout", "content", "speaker_notes"],
                            "properties": {
                                "id": {"type": "string", "description": "Stable lowercase slide id unique within the deck."},
                                "title": {"type": "string", "description": "Short slide title, ideally <= 22 Chinese characters."},
                                "layout": {
                                    "type": "string",
                                    "description": "Renderer layout. Use only canonical layout names, never legacy aliases.",
                                    "enum": [
                                        "cover",
                                        "split",
                                        "flow",
                                        "metrics",
                                        "cards",
                                        "table",
                                        "summary",
                                        "timeline",
                                    ],
                                },
                                "asset_key": {"type": "string", "description": "Optional key into slide_draft.assets for an image; empty when no image is needed."},
                                "visual": {
                                    "type": "object",
                                    "description": "Optional renderer hint. Only highlightIndex is consumed.",
                                    "additionalProperties": False,
                                    "required": ["highlightIndex"],
                                    "properties": {
                                        "highlightIndex": {"type": "integer", "description": "Flow step index to highlight, or -1 for no highlight."},
                                    },
                                },
                                "speaker_notes": {"type": "string", "description": "Brief presenter note. The current renderer keeps it as metadata/fallback text only."},
                                "content": {
                                    "type": "object",
                                    "description": "Layout-specific content. Include only fields consumed by the selected layout.",
                                    "additionalProperties": False,
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
                "description": "Short title/body pair for cards or flow steps.",
                "additionalProperties": False,
                "required": ["title", "body"],
                "properties": {
                    "title": {"type": "string", "description": "Short card or step title."},
                    "body": {"type": "string", "description": "Short body text; use table layout when this needs to be long."},
                },
            },
            "metric": {
                "type": "object",
                "description": "One metric card item.",
                "additionalProperties": False,
                "required": ["label", "value", "note"],
                "properties": {
                    "label": {"type": "string", "description": "Metric label."},
                    "value": {"type": "string", "description": "Metric value."},
                    "note": {"type": "string", "description": "Short metric note or target."},
                },
            },
            "event": {
                "type": "object",
                "description": "One timeline event.",
                "additionalProperties": False,
                "required": ["date", "title", "body"],
                "properties": {
                    "date": {"type": "string", "description": "Date, phase, or time label."},
                    "title": {"type": "string", "description": "Short event title."},
                    "body": {"type": "string", "description": "Short event description."},
                },
            },
        },
    },
}

BOARD_SECTION_KINDS = {"overview", "flow", "cards", "metrics", "table", "timeline", "summary"}

BOARD_IR_SECTION_PROPERTIES = {
    "id": {"type": "string", "description": "Stable section id unique in this board."},
    "kind": {
        "type": "string",
        "description": "Board section kind consumed by renderer.",
        "enum": ["overview", "flow", "cards", "metrics", "table", "timeline", "summary"],
    },
    "title": {"type": "string", "description": "Short section title."},
    "description": {"type": "string", "description": "One concise section explanation."},
    "accent": {"type": "string", "description": "Optional section accent color in #RRGGBB format."},
    "items": {"type": "array", "description": "Short lines for overview/cards/summary sections.", "items": {"type": "string", "description": "One concise line."}},
    "nodes": {"type": "array", "description": "Flow nodes for flow sections.", "items": {"$ref": "#/$defs/board_node"}},
    "edges": {"type": "array", "description": "Flow edges for flow sections.", "items": {"$ref": "#/$defs/board_edge"}},
    "metrics": {"type": "array", "description": "Metric cards for metrics sections.", "items": {"$ref": "#/$defs/board_metric"}},
    "columns": {"type": "array", "description": "Table headers for table sections.", "items": {"type": "string", "description": "Header label."}},
    "rows": {"type": "array", "description": "Table rows for table sections.", "items": {"type": "array", "items": {"type": "string", "description": "Cell text."}}},
    "events": {"type": "array", "description": "Timeline events for timeline sections.", "items": {"$ref": "#/$defs/board_event"}},
}

BOARD_IR_RESPONSE_SCHEMA = {
    "name": "planb_board_ir_response",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["board_ir", "warnings"],
        "properties": {
            "warnings": {"type": "array", "items": {"type": "string"}},
            "board_ir": {
                "type": "object",
                "description": "Renderer-ready board IR used to build whiteboard DSL.",
                "additionalProperties": False,
                "required": ["title", "file_name", "subtitle", "theme", "sections"],
                "properties": {
                    "title": {"type": "string", "description": "Board title shown in the top frame."},
                    "file_name": {"type": "string", "description": "Output board file name without extension."},
                    "subtitle": {"type": "string", "description": "Board subtitle context."},
                    "theme": {
                        "type": "object",
                        "description": "Board palette tokens.",
                        "additionalProperties": False,
                        "required": ["accent", "accent2", "background", "surface", "text", "muted"],
                        "properties": {
                            "accent": {"type": "string", "description": "Primary accent color #RRGGBB."},
                            "accent2": {"type": "string", "description": "Secondary accent color #RRGGBB."},
                            "background": {"type": "string", "description": "Board background color #RRGGBB."},
                            "surface": {"type": "string", "description": "Card/frame surface color #RRGGBB."},
                            "text": {"type": "string", "description": "Primary text color #RRGGBB."},
                            "muted": {"type": "string", "description": "Muted text color #RRGGBB."},
                        },
                    },
                    "sections": {
                        "type": "array",
                        "description": "Ordered board sections consumed by the whiteboard renderer.",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["id", "kind", "title", "description", "accent", "items", "nodes", "edges", "metrics", "columns", "rows", "events"],
                            "properties": BOARD_IR_SECTION_PROPERTIES,
                        },
                    },
                },
            },
        },
        "$defs": {
            "board_node": {
                "type": "object",
                "description": "Flow node in a board flow section.",
                "additionalProperties": False,
                "required": ["id", "label"],
                "properties": {
                    "id": {"type": "string", "description": "Stable node id."},
                    "label": {"type": "string", "description": "Node label text."},
                },
            },
            "board_edge": {
                "type": "object",
                "description": "Directed edge between flow nodes.",
                "additionalProperties": False,
                "required": ["from", "to"],
                "properties": {
                    "from": {"type": "string", "description": "Source node id."},
                    "to": {"type": "string", "description": "Target node id."},
                },
            },
            "board_metric": {
                "type": "object",
                "description": "Metric item rendered as metric card.",
                "additionalProperties": False,
                "required": ["label", "value", "note"],
                "properties": {
                    "label": {"type": "string", "description": "Metric label."},
                    "value": {"type": "string", "description": "Metric value."},
                    "note": {"type": "string", "description": "Metric note."},
                },
            },
            "board_event": {
                "type": "object",
                "description": "Timeline event rendered in timeline section.",
                "additionalProperties": False,
                "required": ["date", "title", "body"],
                "properties": {
                    "date": {"type": "string", "description": "Date or phase label."},
                    "title": {"type": "string", "description": "Event title."},
                    "body": {"type": "string", "description": "Event detail text."},
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
        fallback_ir = _ensure_ir_defaults(_summary_ir_from_request(payload), payload)
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
    repair_warnings = _repair_slide_draft(slide_draft)
    warnings.extend(repair_warnings)
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


def generate_board_ir_from_content_ir(content_ir: dict, options: dict | None = None) -> dict:
    started_at = time.perf_counter()
    payload = {"content_ir": content_ir if isinstance(content_ir, dict) else {}, "options": options or {}}
    llm_result = _call_llm_json(
        payload,
        BOARD_IR_PROMPT,
        BOARD_IR_RESPONSE_SCHEMA,
        model_override=_ppt_model_from_options(options),
        purpose="board_ir",
    )
    warnings = list(llm_result.get("warnings") or [])
    if llm_result.get("ok") is not True:
        return _finish("generate_board_ir", llm_result, started_at)

    board_ir = _extract_named_object(llm_result.get("data") or {}, "board_ir")
    board_ir = _ensure_board_ir_defaults(board_ir, payload["content_ir"])
    board_ir = _normalize_board_ir(board_ir)
    repair_warnings = _repair_board_ir(board_ir, payload["content_ir"])
    warnings.extend(repair_warnings)
    validation = _validate_board_ir(board_ir)
    if validation:
        return _finish(
            "generate_board_ir",
            _error("BOARD_IR_INVALID", "LLM returned invalid BoardIR.", validation, warnings),
            started_at,
            debug=llm_result.get("debug"),
        )

    return _finish(
        "done",
        {
            "ok": True,
            "board_ir": board_ir,
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
    out["description"] = str(out.get("description") or out.get("summary") or "").strip()
    out["intent"] = str(out.get("intent") or out.get("purpose") or "").strip()
    out["sourceRefs"] = _string_list_from_any(out.get("sourceRefs") or out.get("source_refs") or out.get("sources"))

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
        elif isinstance(out.get("cards"), list):
            out["cards"] = _card_list_from_any(out.get("cards"))
    elif kind == "metrics":
        items = _metric_list_from_any(out.get("items") or out.get("metrics") or out.get("cards") or content)
        if items:
            out["items"] = items
    elif kind == "table":
        if not isinstance(out.get("columns"), list):
            out["columns"] = _string_list_from_any(out.get("columns") or out.get("headers"))
        if not isinstance(out.get("rows"), list):
            out["rows"] = _rows_from_any(out.get("rows") or out.get("items") or content)
        out["columns"] = _string_list_from_any(out.get("columns"))
        out["rows"] = _normalize_table_rows(out.get("rows"), len(out["columns"]))
    elif kind == "timeline":
        events = _event_list_from_any(out.get("events") or out.get("items") or out.get("timeline") or content)
        if events:
            out["events"] = events
        elif isinstance(out.get("events"), list):
            out["events"] = _event_list_from_any(out.get("events"))
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


def _edges_from_any(value: Any, nodes: list[JsonDict]) -> list[JsonDict]:
    node_ids = {str(node.get("id")) for node in nodes if isinstance(node, dict) and node.get("id")}
    output: list[JsonDict] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            source = str(item.get("from") or "")
            target = str(item.get("to") or "")
        elif isinstance(item, list) and len(item) >= 2:
            source = str(item[0] or "")
            target = str(item[1] or "")
        else:
            continue
        if source and target and source in node_ids and target in node_ids:
            output.append({"from": source, "to": target})
    return output or _linear_edges_for_nodes(nodes)


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
    meta.setdefault("file_name", _safe_output_name(title))
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
        _ensure_block_defaults(block)
    return out


def _ensure_block_defaults(block: JsonDict) -> None:
    block.setdefault("description", "")
    block.setdefault("intent", "")
    if not isinstance(block.get("sourceRefs"), list):
        block["sourceRefs"] = _string_list_from_any(block.get("sourceRefs") or block.get("source_refs"))
    kind = block.get("kind")
    for key in ("subtitle", "kicker", "caption", "image"):
        block.setdefault(key, "")
    for key in ("points", "nodes", "edges", "items", "cards", "columns", "rows", "events"):
        if not isinstance(block.get(key), list):
            block[key] = []
    if kind == "cards":
        block["cards"] = _card_list_from_any(block.get("cards"))
    elif kind == "timeline":
        block["events"] = _event_list_from_any(block.get("events"))
    elif kind == "table":
        block["columns"] = _string_list_from_any(block.get("columns"))
        block["rows"] = _normalize_table_rows(block.get("rows"), len(block["columns"]))


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
    out.setdefault("file_name", _safe_output_name(str(out.get("title") or content_ir.get("title") or "presentation")))
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
        slide.setdefault("layout", "summary")
        slide["layout"] = _canonical_slide_layout(str(slide.get("layout") or ""))
        slide.setdefault("asset_key", "")
        if not isinstance(slide.get("visual"), dict):
            slide["visual"] = {"highlightIndex": -1}
        else:
            visual = slide["visual"]
            visual.setdefault("highlightIndex", -1)
        if not isinstance(slide.get("content"), dict):
            slide["content"] = {}
        layout = str(slide.get("layout") or "")
        slide["content"] = _normalize_slide_content(layout, slide["content"])
        slide.setdefault("speaker_notes", "")
    return out


def _ensure_board_ir_defaults(board_ir: JsonDict, content_ir: JsonDict) -> JsonDict:
    out = json.loads(json.dumps(board_ir if isinstance(board_ir, dict) else {}, ensure_ascii=False))
    title = str(out.get("title") or content_ir.get("title") or "Solution Board")
    out.setdefault("title", title)
    out.setdefault("file_name", _safe_output_name(title))
    out.setdefault("subtitle", str(content_ir.get("subtitle") or ""))
    theme = out.get("theme")
    if not isinstance(theme, dict):
        theme = {}
        out["theme"] = theme
    theme.setdefault("accent", "#2F6BFF")
    theme.setdefault("accent2", "#7C3AED")
    theme.setdefault("background", "#F8FAFC")
    theme.setdefault("surface", "#FFFFFF")
    theme.setdefault("text", "#0F172A")
    theme.setdefault("muted", "#64748B")
    sections = out.get("sections")
    if not isinstance(sections, list):
        sections = []
        out["sections"] = sections
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        section.setdefault("id", f"section_{index + 1}")
        section.setdefault("kind", "overview")
        section.setdefault("title", str(section.get("id") or f"Section {index + 1}"))
        section.setdefault("description", "")
        section.setdefault("accent", "")
        for key in ("items", "nodes", "edges", "metrics", "columns", "rows", "events"):
            if not isinstance(section.get(key), list):
                section[key] = []
    return out


def _normalize_board_ir(board_ir: JsonDict) -> JsonDict:
    out = json.loads(json.dumps(board_ir if isinstance(board_ir, dict) else {}, ensure_ascii=False))
    sections = out.get("sections")
    if not isinstance(sections, list):
        out["sections"] = []
        return out
    normalized_sections: list[JsonDict] = []
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        kind = str(section.get("kind") or "").strip()
        if kind not in BOARD_SECTION_KINDS:
            kind = "overview"
        clean: JsonDict = {
            "id": str(section.get("id") or f"section_{index + 1}"),
            "kind": kind,
            "title": str(section.get("title") or f"Section {index + 1}"),
            "description": str(section.get("description") or ""),
            "accent": str(section.get("accent") or ""),
            "items": [],
            "nodes": [],
            "edges": [],
            "metrics": [],
            "columns": [],
            "rows": [],
            "events": [],
        }
        if kind in {"overview", "cards", "summary"}:
            clean["items"] = _string_list_from_any(section.get("items"))
        elif kind == "flow":
            clean["nodes"] = _flow_nodes_from_any(section.get("nodes") or section.get("items"))
            clean["edges"] = _linear_edges_for_nodes(clean["nodes"])
            if isinstance(section.get("edges"), list) and section.get("edges"):
                clean["edges"] = _edges_from_any(section.get("edges"), clean["nodes"])
        elif kind == "metrics":
            clean["metrics"] = _metric_list_from_any(section.get("metrics") or section.get("items"))
        elif kind == "table":
            clean["columns"] = _string_list_from_any(section.get("columns"))
            clean["rows"] = _normalize_table_rows(section.get("rows"), len(clean["columns"]))
        elif kind == "timeline":
            clean["events"] = _board_event_list_from_any(section.get("events") or section.get("items"))
        normalized_sections.append(clean)
    out["sections"] = normalized_sections
    return out


def _repair_board_ir(board_ir: JsonDict, content_ir: JsonDict | None = None) -> list[str]:
    warnings: list[str] = []
    content = content_ir if isinstance(content_ir, dict) else {}
    sections = board_ir.get("sections")
    if not isinstance(sections, list):
        return warnings
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        kind = str(section.get("kind") or "overview")
        before = _board_section_is_renderable(section)
        if kind in {"overview", "cards", "summary"} and not section.get("items"):
            section["items"] = _board_fallback_items(section, content)
        elif kind == "flow":
            if not section.get("nodes"):
                labels = _board_fallback_items(section, content, limit=4)
                section["nodes"] = [{"id": f"n{node_index + 1}", "label": label} for node_index, label in enumerate(labels)]
            if not section.get("edges"):
                section["edges"] = _linear_edges_for_nodes(section.get("nodes") or [])
        elif kind == "metrics" and not section.get("metrics"):
            metrics = _metric_list_from_any(content.get("metrics"))
            section["metrics"] = metrics or [{"label": str(section.get("title") or "指标"), "value": "待确认", "note": _board_first_fallback_line(section, content)}]
        elif kind == "table":
            if not section.get("columns"):
                section["columns"] = ["事项", "说明"]
            if not section.get("rows"):
                section["rows"] = [[line, "待确认"] for line in _board_fallback_items(section, content, limit=4)]
            section["rows"] = _normalize_table_rows(section.get("rows"), len(section.get("columns") or []))
        elif kind == "timeline" and not section.get("events"):
            events = _board_event_list_from_any(content.get("implementation_plan"))
            section["events"] = events or [{"date": "近期", "title": str(section.get("title") or "事项"), "body": _board_first_fallback_line(section, content)}]
        if not before and _board_section_is_renderable(section):
            warnings.append(f"Repaired board sections[{index}] {kind} content with deterministic fallback items.")
    return warnings


def _board_section_is_renderable(section: JsonDict) -> bool:
    kind = str(section.get("kind") or "")
    if kind in {"overview", "cards", "summary"}:
        return bool(section.get("items"))
    if kind == "flow":
        return bool(section.get("nodes")) and bool(section.get("edges"))
    if kind == "metrics":
        return bool(section.get("metrics"))
    if kind == "table":
        return bool(section.get("columns")) and bool(section.get("rows"))
    if kind == "timeline":
        return bool(section.get("events"))
    return False


def _board_fallback_items(section: JsonDict, content_ir: JsonDict, limit: int = 5) -> list[str]:
    candidates: list[str] = []
    for value in (section.get("description"), section.get("title")):
        text = str(value or "").strip()
        if text:
            candidates.append(text)
    kind = str(section.get("kind") or "")
    title = str(section.get("title") or "").lower()
    if kind == "overview" or "背景" in title or "概览" in title or "overview" in title:
        candidates.extend(_string_list_from_any(content_ir.get("background") or content_ir.get("subtitle")))
    if kind in {"cards", "summary"}:
        candidates.extend(_cards_to_lines(content_ir.get("problems")))
        candidates.extend(_cards_to_lines(content_ir.get("solution_modules")))
        candidates.extend(_string_list_from_any(content_ir.get("expected_outcomes")))
        candidates.extend(_string_list_from_any(content_ir.get("decision_points")))
    if kind == "flow":
        candidates.extend(_cards_to_lines(content_ir.get("process")))
        candidates.extend(_cards_to_lines(content_ir.get("solution_modules")))
    if kind == "table":
        candidates.extend(_cards_to_lines(content_ir.get("risks")))
        candidates.extend(_string_list_from_any(content_ir.get("decision_points")))
    if kind == "timeline":
        candidates.extend(_events_to_lines(content_ir.get("implementation_plan")))
    clean = [line for line in (str(item or "").strip() for item in candidates) if line]
    return clean[:limit] or ["待确认"]


def _board_first_fallback_line(section: JsonDict, content_ir: JsonDict) -> str:
    return _board_fallback_items(section, content_ir, limit=1)[0]


def _cards_to_lines(value: Any) -> list[str]:
    lines: list[str] = []
    for item in _card_list_from_any(value):
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or item.get("description") or "").strip()
        if title and body:
            lines.append(f"{title}: {body}")
        elif title or body:
            lines.append(title or body)
    return lines


def _events_to_lines(value: Any) -> list[str]:
    lines: list[str] = []
    for item in _board_event_list_from_any(value):
        date_text = str(item.get("date") or "").strip()
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or item.get("description") or "").strip()
        line = " ".join(part for part in (date_text, title) if part)
        if body:
            line = f"{line}: {body}" if line else body
        if line:
            lines.append(line)
    return lines


def _board_event_list_from_any(value: Any) -> list[JsonDict]:
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
        elif str(item).strip():
            output.append({"date": "", "title": str(item).strip(), "body": ""})
    return [item for item in output if item["date"] or item["title"] or item["body"]]


def repair_board_ir(board_ir: JsonDict, content_ir: JsonDict | None = None) -> tuple[JsonDict, list[str]]:
    repaired = _ensure_board_ir_defaults(board_ir if isinstance(board_ir, dict) else {}, content_ir or {})
    repaired = _normalize_board_ir(repaired)
    warnings = _repair_board_ir(repaired, content_ir or {})
    return repaired, warnings


def _canonical_slide_layout(layout: str) -> str:
    value = str(layout or "").strip()
    return SLIDE_LAYOUT_ALIASES.get(value, value)


def _repair_slide_draft(slide_draft: JsonDict) -> list[str]:
    warnings: list[str] = []
    slides = slide_draft.get("slides")
    if not isinstance(slides, list):
        return warnings
    layouts: list[str] = []
    for index, slide in enumerate(slides):
        if not isinstance(slide, dict):
            layouts.append("")
            continue
        layout = _canonical_slide_layout(str(slide.get("layout") or "summary"))
        slide["layout"] = layout
        if len(layouts) >= 2 and layout and layouts[-1] == layouts[-2] == layout:
            original = layout
            _repair_repeated_slide_layout(slide, index)
            layout = str(slide.get("layout") or "summary")
            warnings.append(f"Repaired slides[{index}] layout from {original} to {layout} to avoid 3 consecutive {original} slides.")
        if isinstance(slide.get("content"), dict):
            slide["content"] = _normalize_slide_content(str(slide.get("layout") or ""), slide["content"])
        layouts.append(str(slide.get("layout") or ""))
    return warnings


def _repair_repeated_slide_layout(slide: JsonDict, index: int) -> None:
    layout = str(slide.get("layout") or "")
    content = _as_dict(slide.get("content"))
    if layout == "table":
        replacement = _table_slide_replacement(content)
    elif layout == "timeline":
        replacement = ("summary", _summary_content_from_events(content.get("events")))
    elif layout in {"cards", "flow"}:
        key = "cards" if layout == "cards" else "steps"
        replacement = ("summary", _summary_content_from_cards(content.get(key)))
    elif layout == "metrics":
        replacement = ("summary", _summary_content_from_metrics(content.get("metrics")))
    else:
        replacement = ("summary", _summary_content_from_any(content))
    slide["layout"] = replacement[0]
    slide["content"] = replacement[1]
    if not str(slide.get("id") or "").strip():
        slide["id"] = f"slide_{index + 1}"


def _table_slide_replacement(content: JsonDict) -> tuple[str, JsonDict]:
    columns = _string_list_from_any(content.get("columns"))
    rows = _normalize_table_rows(content.get("rows"), len(columns)) if columns else _rows_from_any(content.get("rows"))
    if _table_rows_look_like_timeline(columns, rows):
        return "timeline", {"events": _events_from_table_rows(columns, rows)}
    if 0 < len(rows) <= 4 and len(columns) <= 3 and _max_nested_text_len(rows) <= 36:
        return "cards", {"cards": _cards_from_table_rows(columns, rows)}
    return "summary", _summary_content_from_table(columns, rows)


def _table_rows_look_like_timeline(columns: list[str], rows: list[list[str]]) -> bool:
    labels = " ".join(columns).lower()
    if any(token in labels for token in ("时间", "日期", "阶段", "排期", "周期", "date", "time", "phase", "week")):
        return True
    first_values = " ".join(row[0] for row in rows if row)
    return bool(re.search(r"(第[一二三四五六七八九十\d]+阶段|\d+\s*[-~至]\s*\d+\s*周|周|月|q[1-4]|近期|现在)", first_values, flags=re.I))


def _events_from_table_rows(columns: list[str], rows: list[list[str]]) -> list[JsonDict]:
    events: list[JsonDict] = []
    for row in rows[:5]:
        values = [str(cell or "").strip() for cell in row]
        if not any(values):
            continue
        date = values[0] if values else "近期"
        title = values[1] if len(values) > 1 and values[1] else date
        body_values = values[2:] if len(values) > 2 else values[1:]
        body = "；".join(_label_value_pairs(columns[2:], body_values)) or (values[-1] if len(values) > 1 else title)
        events.append({"date": date or "近期", "title": title or "事项", "body": body or "待确认"})
    return events or [{"date": "近期", "title": "排期", "body": "待确认"}]


def _cards_from_table_rows(columns: list[str], rows: list[list[str]]) -> list[JsonDict]:
    cards: list[JsonDict] = []
    for row in rows[:4]:
        values = [str(cell or "").strip() for cell in row]
        if not any(values):
            continue
        title = values[0] or "事项"
        body = "；".join(_label_value_pairs(columns[1:], values[1:])) or "待确认"
        cards.append({"title": title, "body": body})
    return cards or [{"title": "事项", "body": "待确认"}]


def _summary_content_from_table(columns: list[str], rows: list[list[str]]) -> JsonDict:
    lines = []
    for row in rows:
        values = [str(cell or "").strip() for cell in row]
        if any(values):
            lines.append("；".join(_label_value_pairs(columns, values)) or "；".join(values))
    return _split_summary_lines(lines)


def _summary_content_from_cards(value: Any) -> JsonDict:
    cards = _slide_card_list_from_any(value)
    return _split_summary_lines([f"{card.get('title')}: {card.get('body')}" for card in cards])


def _summary_content_from_metrics(value: Any) -> JsonDict:
    metrics = _metric_list_from_any(value)
    return _split_summary_lines([f"{item.get('label')}: {item.get('value')} {item.get('note')}".strip() for item in metrics])


def _summary_content_from_events(value: Any) -> JsonDict:
    events = _event_list_from_any(value)
    return _split_summary_lines([f"{event.get('date')} {event.get('title')}: {event.get('body')}" for event in events])


def _summary_content_from_any(content: JsonDict) -> JsonDict:
    lines: list[str] = []
    for value in content.values():
        lines.extend(_string_list_from_any(value))
    return _split_summary_lines(lines)


def _split_summary_lines(lines: list[str]) -> JsonDict:
    clean = [line for line in (str(item or "").strip() for item in lines) if line]
    if not clean:
        clean = ["待确认"]
    midpoint = max(1, (len(clean) + 1) // 2)
    return {"outcomes": clean[:midpoint], "next_steps": clean[midpoint:] or clean[:1]}


def _label_value_pairs(labels: list[str], values: list[str]) -> list[str]:
    pairs: list[str] = []
    for index, value in enumerate(values):
        text = str(value or "").strip()
        if not text:
            continue
        label = str(labels[index] if index < len(labels) else "").strip()
        pairs.append(f"{label}: {text}" if label else text)
    return pairs


def _max_nested_text_len(rows: list[list[str]]) -> int:
    maximum = 0
    for row in rows:
        for cell in row:
            maximum = max(maximum, len(str(cell or "")))
    return maximum


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
        if _requires_table_intent(block) and kind != "table":
            errors.append(f"Block {block_id or index} intent {block.get('intent')} must use kind table.")
        if not isinstance(block.get("sourceRefs"), list):
            errors.append(f"Block {block_id or index} sourceRefs must be an array.")
        if kind == "table":
            columns = block.get("columns")
            rows = block.get("rows")
            if not isinstance(columns, list) or not columns or not any(str(col).strip() for col in columns):
                errors.append(f"Table block {block_id or index} columns must contain at least one header.")
            if not isinstance(rows, list):
                errors.append(f"Table block {block_id or index} rows must be an array.")
            elif rows and columns:
                width = len(columns)
                for row_index, row in enumerate(rows):
                    if not isinstance(row, list):
                        errors.append(f"Table block {block_id or index} rows[{row_index}] must be an array.")
                    elif len(row) != width:
                        errors.append(f"Table block {block_id or index} rows[{row_index}] must match columns length.")
        elif kind == "cards":
            cards = block.get("cards")
            if not isinstance(cards, list):
                errors.append(f"Cards block {block_id or index} cards must be an array.")
            else:
                for card_index, card in enumerate(cards):
                    if not isinstance(card, dict):
                        errors.append(f"Cards block {block_id or index} cards[{card_index}] must be an object.")
                    elif not (str(card.get("title") or "").strip() or str(card.get("body") or "").strip()):
                        errors.append(f"Cards block {block_id or index} cards[{card_index}] must contain title or body.")
    return errors


def _requires_table_intent(block: JsonDict) -> bool:
    return str(block.get("intent") or "").strip().lower() in {"actions", "risks", "comparison", "metrics"}


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
        return bool(_as_list(block.get("columns")) and (_as_list(block.get("rows")) or str(block.get("caption") or block.get("description") or "").strip()))
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
    allowed_deck_keys = {"title", "file_name", "subtitle", "theme", "assets", "slides"}
    for key in slide_draft:
        if key not in allowed_deck_keys:
            errors.append(f"slide_draft.{key} is not supported.")
    if not str(slide_draft.get("title") or "").strip():
        errors.append("slide_draft.title is required.")
    theme = slide_draft.get("theme")
    if not isinstance(theme, dict):
        errors.append("slide_draft.theme must be an object.")
    else:
        for key in theme:
            if key not in THEME_COLOR_FIELDS and key != "name":
                errors.append(f"slide_draft.theme.{key} is not supported.")
    slides = slide_draft.get("slides")
    if not isinstance(slides, list) or not slides:
        errors.append("slide_draft.slides must be a non-empty array.")
        return errors
    layouts: list[str] = []
    for index, slide in enumerate(slides):
        if not isinstance(slide, dict):
            errors.append(f"slides[{index}] must be an object.")
            continue
        allowed_slide_keys = {"id", "title", "layout", "content", "asset_key", "visual", "speaker_notes"}
        for key in slide:
            if key not in allowed_slide_keys:
                errors.append(f"slides[{index}].{key} is not supported.")
        layout = str(slide.get("layout") or "").strip()
        layouts.append(layout)
        for key in ("id", "title", "layout"):
            if not str(slide.get(key) or "").strip():
                errors.append(f"slides[{index}].{key} is required.")
        if layout not in SLIDE_LAYOUTS:
            errors.append(f"Unsupported slide layout: {layout}.")
        visual = slide.get("visual")
        if visual is not None:
            if not isinstance(visual, dict):
                errors.append(f"slides[{index}].visual must be an object.")
            else:
                for key in visual:
                    if key != "highlightIndex":
                        errors.append(f"slides[{index}].visual.{key} is not supported.")
                if "highlightIndex" in visual and not isinstance(visual.get("highlightIndex"), int):
                    errors.append(f"slides[{index}].visual.highlightIndex must be an integer.")
        content = slide.get("content")
        if not isinstance(content, dict) or not content:
            errors.append(f"slides[{index}].content must be a non-empty object.")
        else:
            _validate_slide_content(index, layout, content, errors)
    for index in range(2, len(layouts)):
        if layouts[index] == layouts[index - 1] == layouts[index - 2]:
            errors.append(f"slides[{index - 2}:{index + 1}] reuse layout {layouts[index]} 3 times.")
    return errors


def validate_slide_draft(slide_draft: JsonDict) -> list[str]:
    return _validate_slide_draft(slide_draft)


def repair_slide_draft(slide_draft: JsonDict) -> tuple[JsonDict, list[str]]:
    repaired = json.loads(json.dumps(slide_draft if isinstance(slide_draft, dict) else {}, ensure_ascii=False))
    warnings = _repair_slide_draft(repaired)
    return repaired, warnings


def _validate_board_ir(board_ir: JsonDict) -> list[str]:
    errors: list[str] = []
    if not isinstance(board_ir, dict):
        return ["BoardIR must be an object."]
    for key in ("title", "file_name", "subtitle"):
        if not str(board_ir.get(key) or "").strip():
            errors.append(f"board_ir.{key} is required.")
    theme = board_ir.get("theme")
    if not isinstance(theme, dict):
        errors.append("board_ir.theme must be an object.")
    else:
        for key in ("accent", "accent2", "background", "surface", "text", "muted"):
            if not str(theme.get(key) or "").strip():
                errors.append(f"board_ir.theme.{key} is required.")
    sections = board_ir.get("sections")
    if not isinstance(sections, list) or not sections:
        errors.append("board_ir.sections must be a non-empty array.")
        return errors
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            errors.append(f"sections[{index}] must be an object.")
            continue
        kind = str(section.get("kind") or "")
        if kind not in BOARD_SECTION_KINDS:
            errors.append(f"sections[{index}].kind is not supported: {kind}.")
            continue
        if not str(section.get("id") or "").strip():
            errors.append(f"sections[{index}].id is required.")
        if not str(section.get("title") or "").strip():
            errors.append(f"sections[{index}].title is required.")
        if kind in {"overview", "cards", "summary"} and not section.get("items"):
            errors.append(f"sections[{index}].items must be non-empty for {kind}.")
        if kind == "flow":
            nodes = section.get("nodes")
            edges = section.get("edges")
            if not isinstance(nodes, list) or not nodes:
                errors.append(f"sections[{index}].nodes must be non-empty for flow.")
            if not isinstance(edges, list) or not edges:
                errors.append(f"sections[{index}].edges must be non-empty for flow.")
        if kind == "metrics" and not section.get("metrics"):
            errors.append(f"sections[{index}].metrics must be non-empty for metrics.")
        if kind == "table":
            columns = section.get("columns")
            rows = section.get("rows")
            if not isinstance(columns, list) or not columns:
                errors.append(f"sections[{index}].columns must be non-empty for table.")
            if not isinstance(rows, list) or not rows:
                errors.append(f"sections[{index}].rows must be non-empty for table.")
        if kind == "timeline" and not section.get("events"):
            errors.append(f"sections[{index}].events must be non-empty for timeline.")
    return errors


def validate_board_ir(board_ir: JsonDict) -> list[str]:
    return _validate_board_ir(board_ir)


def _validate_slide_content(index: int, layout: str, content: JsonDict, errors: list[str]) -> None:
    allowed_by_layout = {
        "cover": {"subtitle", "kicker", "owner", "audience"},
        "split": {"points"},
        "cards": {"cards"},
        "metrics": {"metrics"},
        "flow": {"steps"},
        "table": {"columns", "rows"},
        "timeline": {"events"},
        "summary": {"next_steps", "outcomes"},
    }
    allowed = allowed_by_layout.get(layout, set())
    for key in content:
        if key not in allowed:
            errors.append(f"slides[{index}].content.{key} is not supported for layout {layout}.")
    if not _slide_content_has_renderable_items(layout, content):
        errors.append(f"slides[{index}].content does not match layout schema for {layout}.")
    if layout in {"cards", "flow"}:
        key = "cards" if layout == "cards" else "steps"
        for item_index, item in enumerate(content.get(key) or []):
            if not isinstance(item, dict):
                errors.append(f"slides[{index}].content.{key}[{item_index}] must be an object.")
                continue
            for field in item:
                if field not in {"title", "body"}:
                    errors.append(f"slides[{index}].content.{key}[{item_index}].{field} is not supported.")
    if layout == "table":
        columns = content.get("columns")
        rows = content.get("rows")
        if isinstance(columns, list) and isinstance(rows, list):
            width = len(columns)
            for row_index, row in enumerate(rows):
                if not isinstance(row, list):
                    errors.append(f"slides[{index}].content.rows[{row_index}] must be an array.")
                elif len(row) != width:
                    errors.append(f"slides[{index}].content.rows[{row_index}] must match columns length.")
    if layout == "timeline":
        for event_index, event in enumerate(content.get("events") or []):
            if not isinstance(event, dict):
                errors.append(f"slides[{index}].content.events[{event_index}] must be an object.")
                continue
            for field in event:
                if field not in {"date", "title", "body"}:
                    errors.append(f"slides[{index}].content.events[{event_index}].{field} is not supported.")


def _normalize_slide_content(layout: str, content: JsonDict) -> JsonDict:
    out = dict(content)
    layout = _canonical_slide_layout(layout)
    if layout == "split" and "points" in out:
        out["points"] = _string_list_from_any(out.get("points"))
    elif layout == "cards" and "cards" in out:
        out["cards"] = _slide_card_list_from_any(out.get("cards"))
    elif layout == "metrics" and "metrics" in out:
        out["metrics"] = _metric_list_from_any(out.get("metrics"))
    elif layout == "flow" and "steps" in out:
        out["steps"] = _slide_card_list_from_any(out.get("steps"))
    elif layout == "timeline":
        out["events"] = _event_list_from_any(out.get("events"))
    elif layout == "table":
        if "columns" in out:
            out["columns"] = _string_list_from_any(out.get("columns"))
        if "rows" in out:
            out["rows"] = _rows_from_any(out.get("rows"))
    elif layout == "summary":
        if "outcomes" in out:
            out["outcomes"] = _string_list_from_any(out.get("outcomes"))
        if "next_steps" in out:
            out["next_steps"] = _string_list_from_any(out.get("next_steps"))
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
    if layout == "cards":
        cards = _card_list_from_any(
            content_ir.get("problems")
            or content_ir.get("solution_modules")
            or content_ir.get("risks")
            or [{"title": title, "description": background}]
        )
        return {**out, "cards": cards or [{"title": title, "body": background}]}
    if layout == "metrics":
        metrics = _metric_list_from_any(content_ir.get("metrics") or [])
        if not metrics:
            metrics = [{"label": "核心目标", "value": title[:24], "note": background[:80]}]
        return {**out, "metrics": metrics}
    if layout == "flow":
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
    if layout == "table":
        rows = _rows_from_any(content_ir.get("risks") or [])
        if not rows:
            rows = [[title, background]]
        columns = out.get("columns") or ["事项", "说明"]
        return {**out, "columns": columns, "rows": rows}
    if layout == "summary":
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
    if layout == "split":
        return True
    if layout == "cards":
        return bool(content.get("cards"))
    if layout == "metrics":
        return bool(content.get("metrics"))
    if layout == "flow":
        return bool(content.get("steps"))
    if layout == "timeline":
        return bool(content.get("events"))
    if layout == "table":
        return bool(content.get("columns")) and bool(content.get("rows"))
    if layout == "summary":
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
        "fontFace": "Microsoft YaHei",
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
                    "meta": _meta_list_from_any(item.get("meta") or item.get("metadata") or item.get("facts")),
                }
            )
        elif str(item).strip():
            output.append({"title": str(item).strip(), "body": "", "meta": []})
    return [item for item in output if item["title"] or item["body"]]


def _slide_card_list_from_any(value: Any) -> list[JsonDict]:
    return [
        {"title": item["title"], "body": item["body"]}
        for item in _card_list_from_any(value)
    ]


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
            owner = str(item.get("owner") or item.get("assignee") or "").strip()
            status = str(item.get("status") or item.get("state") or "").strip()
            body = str(item.get("body") or item.get("description") or item.get("text") or item.get("note") or "").strip()
            suffix_parts = []
            if owner:
                suffix_parts.append(f"owner: {owner}")
            if status:
                suffix_parts.append(f"status: {status}")
            if suffix_parts:
                body = " | ".join(part for part in [body, ", ".join(suffix_parts)] if part)
            output.append(
                {
                    "date": str(item.get("date") or item.get("time") or item.get("phase") or ""),
                    "title": str(item.get("title") or item.get("label") or item.get("name") or ""),
                    "body": body,
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


def _meta_list_from_any(value: Any) -> list[str]:
    if isinstance(value, dict):
        raw_items = [f"{key}: {val}" for key, val in value.items() if str(val).strip()]
    else:
        raw_items = _string_list_from_any(value)
    return [item for item in (_clean_meta_item(raw) for raw in raw_items) if item]


def _clean_meta_item(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if "待确认" in text or "unknown" in lower or "n/a" == lower:
        return ""
    if "http://" in lower or "https://" in lower:
        return ""
    if "message:" in lower or "source:" in lower or "om_" in lower:
        return ""
    if len(text) > 48:
        return ""
    return text


def _normalize_table_rows(value: Any, width: int) -> list[list[str]]:
    rows = _rows_from_any(value)
    if width <= 0:
        return rows
    normalized = []
    for row in rows:
        cells = [str(cell).strip() or "待确认" for cell in row]
        normalized.append((cells + ["待确认"] * width)[:width])
    return normalized


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_\-]+", "_", value.strip())
    return safe.strip("_") or "task"


def _safe_output_name(value: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", str(value or "").strip())
    safe = re.sub(r"\s+", " ", safe).strip(" ._")
    return safe[:60] or "Generated Presentation"


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
    "generate_board_ir_from_content_ir",
    "generate_content_ir_from_messages",
    "generate_ir_from_messages",
    "generate_slide_draft_from_content_ir",
    "repair_board_ir",
    "repair_slide_draft",
    "validate_board_ir",
    "validate_slide_draft",
]
