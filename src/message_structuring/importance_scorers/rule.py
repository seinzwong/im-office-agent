from __future__ import annotations

import re

from message_structuring.importance_scorers.base import ImportanceScoreResult
from message_structuring.schemas import NormalizedMessage


def _level_from_score(score: float) -> str:
    if score < 0.2:
        return "noise"
    if score < 0.45:
        return "low"
    if score < 0.75:
        return "medium"
    return "high"


class RuleImportanceScorer:
    def __init__(self) -> None:
        self.strong_signal_patterns = {
            "issue": [r"\bbug\b", r"问题", r"异常", r"报错", r"error", r"500"],
            "action_item": [r"\bTODO\b", r"待办", r"需要", r"请.*(完成|处理|跟进)", r"action"],
            "decision": [r"决定", r"结论", r"拍板", r"final decision", r"\bdecision\b"],
            "deadline": [r"截止", r"ddl", r"deadline", r"今天", r"明天", r"本周", r"下周"],
            "risk": [r"风险", r"阻塞", r"卡住", r"rollback", r"影响"],
            "reference": [r"http", r"文档", r"链接", r"参考", r"PRD"],
            "progress": [r"完成", r"进展", r"已上线", r"done", r"已解决"],
            "requirement": [r"需求", r"必须", r"应当", r"规格", r"约束"],
            "solution": [r"方案", r"修复", r"优化", r"实现", r"架构"],
        }
        self.noise_terms = {
            "收到",
            "好的",
            "ok",
            "okay",
            "k",
            "lol",
            "哈哈",
            "嗯嗯",
        }

    def score(self, message: NormalizedMessage) -> ImportanceScoreResult:
        text = (message.content.normalized_text or "").strip()
        lowered = text.lower()
        matched_signals: list[str] = []
        score = 0.0

        if not text:
            return ImportanceScoreResult(
                score=0.0,
                level="noise",
                reason="empty message",
                matched_signals=[],
                backend="rule",
                rule_score=0.0,
            )

        if lowered in self.noise_terms or len(message.content.plain_text.strip()) <= 1:
            return ImportanceScoreResult(
                score=0.05,
                level="noise",
                reason="short/noise signal",
                matched_signals=["noise"],
                backend="rule",
                rule_score=0.05,
            )

        if len(message.content.plain_text.strip()) <= 3:
            score += 0.1
            matched_signals.append("very_short")

        for signal, patterns in self.strong_signal_patterns.items():
            if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
                matched_signals.append(signal)
                score += 0.12

        if message.features.has_mention:
            score += 0.08
            matched_signals.append("mention")
        if message.features.has_url:
            score += 0.08
            matched_signals.append("url")
        if message.features.has_file:
            score += 0.1
            matched_signals.append("file_or_email")
        if len(text) >= 50:
            score += 0.1
            matched_signals.append("long_text")

        score = max(0.0, min(score, 1.0))
        level = _level_from_score(score)
        dedup_signals = sorted(set(matched_signals))
        reason = "matched: " + ", ".join(dedup_signals) if dedup_signals else "no strong signal"

        return ImportanceScoreResult(
            score=score,
            level=level,
            reason=reason,
            matched_signals=dedup_signals,
            backend="rule",
            rule_score=score,
        )
