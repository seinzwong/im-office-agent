from __future__ import annotations

from message_structuring.config import MessageStructuringConfig
from message_structuring.schemas import NormalizedMessage, TopicNode
from message_structuring.topic_backends.base import ModelUnavailableError, TopicAssignResult
from message_structuring.topic_backends.embedding import EmbeddingTopicBackend
from message_structuring.topic_backends.rule import RuleTopicBackend


class HybridTopicBackend:
    def __init__(self, config: MessageStructuringConfig) -> None:
        self.config = config
        self.rule_backend = RuleTopicBackend()
        self.embedding_backend: EmbeddingTopicBackend | None = None
        self.embedding_init_error: str | None = None
        try:
            self.embedding_backend = EmbeddingTopicBackend(
                model_path=config.topic_embedding_model_path,
                device=config.topic_embedding_device,
                assign_threshold=config.topic_embedding_assign_threshold,
                uncertain_threshold=config.topic_embedding_uncertain_threshold,
                max_length=config.topic_embedding_max_length,
            )
        except ModelUnavailableError as exc:
            self.embedding_init_error = str(exc)

    def assign(
        self,
        message: NormalizedMessage,
        task_state: dict | None = None,
        topics: list[TopicNode] | None = None,
        context: dict | None = None,
    ) -> tuple[TopicAssignResult, TopicNode | None]:
        skip, reason = self.rule_backend.should_skip_message(message)
        if skip:
            return (
                TopicAssignResult(
                    status="skipped",
                    topic_id=None,
                    topic_title=None,
                    similarity=0.0,
                    reason=f"backend=hybrid skip_by_rule_policy: {reason}",
                    backend="hybrid",
                    matched_signals=["skip_policy"],
                ),
                None,
            )

        if self.embedding_backend is None:
            rule_res, rule_topic = self.rule_backend.assign(message, task_state, topics, context)
            return (
                TopicAssignResult(
                    status=rule_res.status,
                    topic_id=rule_res.topic_id,
                    topic_title=rule_res.topic_title,
                    similarity=rule_res.similarity,
                    reason=f"backend=hybrid fallback_to_rule because embedding_unavailable: {self.embedding_init_error}",
                    backend="hybrid",
                    matched_signals=rule_res.matched_signals,
                ),
                rule_topic,
            )

        try:
            emb_res, emb_topic = self.embedding_backend.assign(message, task_state, topics, context)
            if emb_res.status == "done":
                return (
                    TopicAssignResult(
                        status="done",
                        topic_id=emb_res.topic_id,
                        topic_title=emb_res.topic_title,
                        similarity=emb_res.similarity,
                        reason=f"backend=hybrid use_embedding: {emb_res.reason}",
                        backend="hybrid",
                        matched_signals=emb_res.matched_signals,
                    ),
                    emb_topic,
                )
        except Exception as exc:
            self.embedding_init_error = str(exc)

        rule_res, rule_topic = self.rule_backend.assign(message, task_state, topics, context)
        return (
            TopicAssignResult(
                status=rule_res.status,
                topic_id=rule_res.topic_id,
                topic_title=rule_res.topic_title,
                similarity=rule_res.similarity,
                reason=f"backend=hybrid fallback_to_rule because embedding_error: {self.embedding_init_error}",
                backend="hybrid",
                matched_signals=rule_res.matched_signals,
            ),
            rule_topic,
        )

    def get_topics(self, task_id: str) -> list[TopicNode]:
        if self.embedding_backend is not None:
            emb_topics = self.embedding_backend.get_topics(task_id)
            if emb_topics:
                return emb_topics
        return self.rule_backend.get_topics(task_id)
