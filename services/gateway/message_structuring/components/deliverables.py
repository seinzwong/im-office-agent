from __future__ import annotations

import re

from services.gateway.message_structuring.schemas import AnnotationStatus, DeliverableAnnotation, NormalizedMessage


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

        self.signal_patterns["deadline"].extend(["截止", "截止时间", "今天", "明天", "后天", "本周", "下周"])
        self.signal_patterns["deliverable_doc"].extend(["方案", "文档", "总结", "汇报", "提案", "材料"])
        self.signal_patterns["task_action"].extend(["需要", "请", "处理", "跟进", "待办", "负责"])
        self.signal_patterns["meeting"].extend(["会议", "同步", "评审", "周会"])
        self.signal_patterns["system_scope"].extend(["接口", "服务", "模块", "系统", "账号", "权限", "流程", "SOP", "CRM"])

    def process(self, message: NormalizedMessage) -> DeliverableAnnotation:
        text = (message.content.normalized_text or "").strip()
        lowered = text.lower()

        base_signals: list[str] = []
        if self.EMAIL_RE.search(text):
            base_signals.append("email")
        if self.DATE_TIME_RE.search(text):
            base_signals.append("time_expr")
        if message.features.has_url:
            base_signals.append("url")
        if message.features.has_file:
            base_signals.append("file")

        for signal, terms in self.signal_patterns.items():
            if any(term in lowered or term in text for term in terms):
                base_signals.append(signal)

        matched_signals = sorted(set(base_signals))
        if message.features.has_mention and matched_signals:
            matched_signals.append("mention")
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
