from __future__ import annotations

import re

from message_structuring.schemas import AnnotationStatus, DeliverableAnnotation, NormalizedMessage


class DeliverableExtractor:
    EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    DATE_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")

    def __init__(self, llm_client: object | None = None) -> None:
        self.llm_client = llm_client
        self.signal_patterns: dict[str, list[str]] = {
            "deadline": ["deadline", "ddl", "截止", "明天", "本周", "下周", "today", "tomorrow"],
            "deliverable_doc": ["prd", "ppt", "文档", "方案", "总结", "汇报", "report"],
            "task_action": ["需要", "请", "处理", "跟进", "todo", "待办", "action item", "我来处理", "我负责"],
            "meeting": ["会议", "meeting", "同步", "评审", "站会", "agenda"],
            "system_scope": ["api", "接口", "服务", "模块", "login", "token", "redis", "rag"],
        }

    def process(self, message: NormalizedMessage) -> DeliverableAnnotation:
        text = (message.content.normalized_text or "").strip()
        lowered = text.lower()

        matched_signals: list[str] = []
        if self.EMAIL_RE.search(text):
            matched_signals.append("email")
        if self.DATE_TIME_RE.search(text):
            matched_signals.append("time_expr")
        if message.features.has_mention:
            matched_signals.append("mention")
        if message.features.has_url:
            matched_signals.append("url")
        if message.features.has_file:
            matched_signals.append("file")

        for signal, terms in self.signal_patterns.items():
            if any(term in lowered or term in text for term in terms):
                matched_signals.append(signal)

        matched_signals = sorted(set(matched_signals))
        has_deliverable = len(matched_signals) > 0 and len(message.content.plain_text.strip()) > 0
        reason = "matched deliverable signals: " + ",".join(matched_signals) if has_deliverable else "no deliverable signal"

        return DeliverableAnnotation(
            status=AnnotationStatus.DONE,
            has_deliverable=has_deliverable,
            items=[],
            matched_signals=matched_signals,
            reason=reason,
        )
