from __future__ import annotations

import re

from message_structuring.schemas import AnnotationStatus, DeliverableAnnotation, DeliverableItem, NormalizedMessage


class DeliverableExtractor:
    EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

    def __init__(self, llm_client: object | None = None) -> None:
        self.llm_client = llm_client
        self.time_terms = ["今天", "明天", "下周", "本周", "月底", "deadline", "ddl", "浠婂ぉ", "鏄庡ぉ", "涓嬪懆", "鏈懆", "鏈堝簳"]
        self.artifact_terms = ["文档", "方案", "PPT", "汇报", "总结", "PRD", "鏂囨。", "鏂规", "姹囨姤", "鎬荤粨"]
        self.system_terms = [
            "接口",
            "服务",
            "数据库",
            "模型",
            "RAG",
            "agent",
            "Redis",
            "BERT",
            "embedding",
            "鎺ュ彛",
            "鏈嶅姟",
            "鏁版嵁搴揱",
            "妯″瀷",
        ]
        self.meeting_terms = ["会议", "评审", "同步", "meeting", "call", "站会"]
        self.task_terms = ["需要", "请", "安排", "处理", "跟进", "todo", "待办", "任务"]
        self.deadline_terms = ["截止", "ddl", "deadline", "前完成", "最晚"]
        self.sender_responsibility_terms = ["我来", "我负责", "我跟进", "鎴戞潵", "鎴戣礋璐"]

    def process(self, message: NormalizedMessage) -> DeliverableAnnotation:
        text = (message.content.normalized_text or "").strip()
        lowered = text.lower()
        items: list[DeliverableItem] = []

        for mention in message.mentions:
            if mention.name:
                items.append(DeliverableItem(label="person", value=mention.name, source="mention"))

        if any(term in text for term in self.sender_responsibility_terms):
            sender_name = message.sender.display_name or message.sender.user_id or message.sender.open_id
            if sender_name:
                items.append(DeliverableItem(label="person", value=sender_name, source="sender_responsibility"))

        for email in self.EMAIL_RE.findall(text):
            items.append(DeliverableItem(label="email", value=email, source="regex"))

        for term in self.time_terms:
            if term.lower() in lowered:
                label = "deadline" if term.lower() in {"deadline", "ddl"} else "time"
                items.append(DeliverableItem(label=label, value=term, source="keyword"))

        for term in self.deadline_terms:
            if term.lower() in lowered:
                items.append(DeliverableItem(label="deadline", value=term, source="keyword"))

        for term in self.artifact_terms:
            if term.lower() in lowered:
                items.append(DeliverableItem(label="artifact", value=term, source="keyword"))

        for term in self.system_terms:
            if term.lower() in lowered:
                items.append(DeliverableItem(label="system_or_module", value=term, source="keyword"))

        for term in self.meeting_terms:
            if term.lower() in lowered:
                items.append(DeliverableItem(label="meeting", value=term, source="keyword"))

        for term in self.task_terms:
            if term.lower() in lowered:
                items.append(DeliverableItem(label="task", value=term, source="keyword"))

        deduped: list[DeliverableItem] = []
        seen = set()
        for item in items:
            key = (item.label, item.value.lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        has_deliverable = len(deduped) > 0
        reason = "matched deliverable rules" if has_deliverable else "no deliverable rule matched"
        return DeliverableAnnotation(
            status=AnnotationStatus.DONE,
            has_deliverable=has_deliverable,
            items=deduped,
            reason=reason,
        )
