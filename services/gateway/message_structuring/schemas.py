from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class AnnotationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"
    NOT_SELECTED = "not_selected"


class SenderInfo(BaseModel):
    sender_type: str = "user"
    union_id: str | None = None
    user_id: str | None = None
    open_id: str | None = None
    display_name: str | None = None


class MentionInfo(BaseModel):
    key: str | None = None
    name: str | None = None
    mentioned_type: str | None = None
    union_id: str | None = None
    user_id: str | None = None
    open_id: str | None = None


class MessageContent(BaseModel):
    normalized_text: str = ""
    plain_text: str = ""
    raw_content: str = ""
    content_parse_status: Literal["ok", "error"] = "ok"


class MessageFeatures(BaseModel):
    has_url: bool = False
    has_file: bool = False
    has_image: bool = False
    has_mention: bool = False
    at_all: bool = False
    text_length: int = 0


class DedupInfo(BaseModel):
    dedup_key: str = ""
    is_duplicate: bool = False


class ImportanceAnnotation(BaseModel):
    status: AnnotationStatus = AnnotationStatus.PENDING
    score: float | None = None
    level: Literal["noise", "low", "medium", "high"] | None = None
    reason: str | None = None
    matched_signals: list[str] = Field(default_factory=list)


class DeliverableItem(BaseModel):
    label: Literal[
        "person",
        "time",
        "deadline",
        "meeting",
        "artifact",
        "email",
        "system_or_module",
        "task",
    ]
    value: str
    source: str | None = None


class DeliverableAnnotation(BaseModel):
    status: AnnotationStatus = AnnotationStatus.PENDING
    has_deliverable: bool | None = None
    items: list[DeliverableItem] = Field(default_factory=list)
    matched_signals: list[str] = Field(default_factory=list)
    reason: str | None = None


class TopicAnnotation(BaseModel):
    status: AnnotationStatus = AnnotationStatus.PENDING
    topic_id: str | None = None
    topic_title: str | None = None
    similarity: float | None = None
    reason: str | None = None


class SummaryAnnotation(BaseModel):
    status: AnnotationStatus = AnnotationStatus.NOT_SELECTED
    summary_item_ids: list[str] = Field(default_factory=list)
    reason: str | None = None


class MessageAnnotations(BaseModel):
    importance: ImportanceAnnotation = Field(default_factory=ImportanceAnnotation)
    deliverables: DeliverableAnnotation = Field(default_factory=DeliverableAnnotation)
    topic: TopicAnnotation = Field(default_factory=TopicAnnotation)
    summary: SummaryAnnotation = Field(default_factory=SummaryAnnotation)


class NormalizedMessage(BaseModel):
    message_id: str
    event_id: str | None = None
    task_id: str
    chat_id: str
    thread_id: str | None = None
    root_id: str | None = None
    parent_id: str | None = None
    chat_type: str = "group"
    source_platform: str = "feishu"
    message_type: str = "text"
    timestamp_ms: int
    update_time_ms: int | None = None
    sender: SenderInfo = Field(default_factory=SenderInfo)
    content: MessageContent = Field(default_factory=MessageContent)
    mentions: list[MentionInfo] = Field(default_factory=list)
    features: MessageFeatures = Field(default_factory=MessageFeatures)
    dedup: DedupInfo = Field(default_factory=DedupInfo)
    annotations: MessageAnnotations = Field(default_factory=MessageAnnotations)


class TaskSession(BaseModel):
    task_id: str
    display_name: str
    status: Literal["collecting", "stopped", "archived"] = "collecting"
    source_chat_id: str
    activation_source: str = "manual_api"
    task_title: str | None = None
    started_at_ms: int
    stopped_at_ms: int | None = None
    stop_reason: str | None = None


class TopicMessageRef(BaseModel):
    message_id: str
    structured_sentence: str
    role: str | None = None


class TopicNode(BaseModel):
    topic_id: str
    topic_title: str
    summary: str = ""
    message_count: int = 0
    update_count: int = 0
    refs: list[TopicMessageRef] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    status: str = "active"


class SummaryItem(BaseModel):
    summary_item_id: str
    topic_id: str | None = None
    text: str
    source_message_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class TaskBrief(BaseModel):
    summary: str = ""
    goal: str = ""
    deliverables: list[str] = Field(default_factory=list)
    deadline: str | None = None
    status: str = "collecting"
    confidence: float = 0.0


class SummarySection(BaseModel):
    items: list[SummaryItem] = Field(default_factory=list)


class QualityMetrics(BaseModel):
    total_messages: int = 0
    processed_messages: int = 0
    summary_candidate_count: int = 0
    topic_count: int = 0
    errors: list[str] = Field(default_factory=list)


class StructuringResult(BaseModel):
    task: TaskSession
    task_brief: TaskBrief = Field(default_factory=TaskBrief)
    summary: SummarySection = Field(default_factory=SummarySection)
    topics: list[TopicNode] = Field(default_factory=list)
    messages: list[NormalizedMessage] = Field(default_factory=list)
    quality: QualityMetrics = Field(default_factory=QualityMetrics)
