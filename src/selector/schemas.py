from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    USER = "user"
    BOT = "bot"
    SYSTEM = "system"


class SummaryType(str, Enum):
    DECISION = "decision"          # 决策
    ACTION_ITEM = "action_item"    # 任务 / 负责人 / 截止时间
    PROGRESS = "progress"          # 进展
    ISSUE = "issue"                # 问题 / bug / 故障
    RISK = "risk"                  # 风险
    SOLUTION = "solution"          # 方案 / 解决办法
    REQUIREMENT = "requirement"    # 需求 / 需求变更
    DEADLINE = "deadline"          # 时间节点
    REFERENCE = "reference"        # 链接 / 文件 / 资料
    QUESTION = "question"          # 有价值的问题
    CHAT = "chat"                  # 普通聊天
    NOISE = "noise"                # 噪音


class SelectorAction(str, Enum):
    DROP = "drop"
    BUFFER = "buffer"
    ENTER_SUMMARY_QUEUE = "enter_summary_queue"


class ChatMessage(BaseModel):
    """
    单条群聊消息。
    """

    message_id: str = Field(..., description="消息唯一 ID")
    group_id: str = Field(..., description="群聊 ID")
    sender_id: str = Field(..., description="发送者 ID")
    sender_name: str | None = Field(default=None, description="发送者名称")
    role: MessageRole = Field(default=MessageRole.USER, description="发送者角色")

    text: str = Field(..., description="消息正文")
    timestamp: float | None = Field(default=None, description="Unix 时间戳，秒")

    reply_to_id: str | None = Field(default=None, description="回复的消息 ID")
    mentioned_user_ids: list[str] = Field(default_factory=list, description="被 @ 的用户")
    at_all: bool = Field(default=False, description="是否 @ 所有人")

    has_url: bool = Field(default=False, description="是否包含链接")
    has_file: bool = Field(default=False, description="是否包含文件")
    has_image: bool = Field(default=False, description="是否包含图片")

    raw: dict[str, Any] = Field(default_factory=dict, description="原始消息 JSON")


class MessageContext(BaseModel):
    """
    当前消息 + 上下文。
    """

    current_message: ChatMessage
    previous_messages: list[ChatMessage] = Field(default_factory=list)
    next_messages: list[ChatMessage] = Field(default_factory=list)


class CandidateScore(BaseModel):
    """
    对单条消息的筛选结果。
    """

    message_id: str
    group_id: str

    score: float = Field(..., ge=0, le=100, description="0-100 summary 价值分")
    level: Literal[0, 1, 2, 3] = Field(..., description="0 噪音，1 普通，2 有价值，3 高价值")

    should_summarize: bool
    action: SelectorAction
    summary_type: SummaryType

    rule_score: float = Field(default=0, ge=0, le=100)
    model_score: float | None = Field(default=None, ge=0, le=100)

    reason: str = Field(default="", description="为什么这样判断")
    matched_keywords: list[str] = Field(default_factory=list)


class SummaryCandidateBlock(BaseModel):
    """
    后续进入 summary 生成模块的候选消息块。
    """

    group_id: str
    topic_id: str | None = None

    trigger_message_id: str
    related_message_ids: list[str]

    messages: list[ChatMessage]

    score: float = Field(..., ge=0, le=100)
    summary_type: SummaryType
    reason: str = ""

    status: Literal["pending", "sent", "dropped"] = "pending"