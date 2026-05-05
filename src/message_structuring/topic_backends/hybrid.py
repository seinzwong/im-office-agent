from __future__ import annotations

from collections import defaultdict

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
        self._topic_centroids: dict[str, dict[str, list[float]]] = defaultdict(dict)
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
        # Rule-first path is authoritative for skip policy and existing-topic assignments.
        rule_res, rule_topic = self.rule_backend.assign(message, task_state, topics, context)

        if rule_res.status == "skipped":
            return (
                TopicAssignResult(
                    status="skipped",
                    topic_id=None,
                    topic_title=None,
                    similarity=0.0,
                    reason=f"backend=hybrid rule_first skipped_by_rule: {rule_res.reason}",
                    backend="hybrid",
                    matched_signals=rule_res.matched_signals,
                ),
                None,
            )

        if rule_res.status == "error":
            return (
                TopicAssignResult(
                    status="error",
                    topic_id=rule_res.topic_id,
                    topic_title=rule_res.topic_title,
                    similarity=rule_res.similarity,
                    reason=f"backend=hybrid rule_first fallback_to_rule: {rule_res.reason}",
                    backend="hybrid",
                    matched_signals=rule_res.matched_signals,
                ),
                rule_topic,
            )

        # If rule assigned to existing topic, keep it and do not ask embedding.
        if "new_topic" not in (rule_res.matched_signals or []):
            return (
                TopicAssignResult(
                    status="done",
                    topic_id=rule_res.topic_id,
                    topic_title=rule_res.topic_title,
                    similarity=rule_res.similarity,
                    reason=f"backend=hybrid rule_first assigned_by_rule: {rule_res.reason}",
                    backend="hybrid",
                    matched_signals=rule_res.matched_signals,
                ),
                rule_topic,
            )

        # Rule created a new topic. Try embedding only to merge into an existing topic.
        if self.embedding_backend is None:
            return (
                TopicAssignResult(
                    status="done",
                    topic_id=rule_res.topic_id,
                    topic_title=rule_res.topic_title,
                    similarity=rule_res.similarity,
                    reason=(
                        "backend=hybrid rule_first fallback_to_rule "
                        f"embedding_unavailable: {self.embedding_init_error}"
                    ),
                    backend="hybrid",
                    matched_signals=rule_res.matched_signals,
                ),
                rule_topic,
            )

        task_id = message.task_id
        created_topic_id = rule_res.topic_id
        existing_topics = [t for t in self.rule_backend.get_topics(task_id) if t.topic_id != created_topic_id]
        if not existing_topics:
            return (
                TopicAssignResult(
                    status="done",
                    topic_id=rule_res.topic_id,
                    topic_title=rule_res.topic_title,
                    similarity=rule_res.similarity,
                    reason=f"backend=hybrid rule_first assigned_by_rule: {rule_res.reason}",
                    backend="hybrid",
                    matched_signals=rule_res.matched_signals,
                ),
                rule_topic,
            )

        try:
            text = (message.content.normalized_text or "").strip() or (message.content.plain_text or "").strip()
            msg_vec = self.embedding_backend._encode(text)
            best_topic = None
            best_similarity = -1.0
            centroids = self._topic_centroids[task_id]
            for topic in existing_topics:
                centroid = centroids.get(topic.topic_id)
                if centroid is None:
                    centroid = self.embedding_backend._encode(topic.topic_title)
                    centroids[topic.topic_id] = centroid
                sim = self.embedding_backend._cosine(msg_vec, centroid)
                if sim > best_similarity:
                    best_similarity = sim
                    best_topic = topic

            if best_topic is not None and best_similarity >= self.config.topic_embedding_assign_threshold and created_topic_id:
                merged = self.rule_backend.reassign_new_topic_to_existing(
                    task_id=task_id,
                    new_topic_id=created_topic_id,
                    target_topic_id=best_topic.topic_id,
                    message=message,
                    role="embedding_merge",
                )
                if merged is not None:
                    self._update_centroid(task_id, merged.topic_id, msg_vec, merged.message_count)
                    return (
                        TopicAssignResult(
                            status="done",
                            topic_id=merged.topic_id,
                            topic_title=merged.topic_title,
                            similarity=round(best_similarity, 4),
                            reason=(
                                "backend=hybrid rule_first assigned_by_embedding "
                                f"similarity={best_similarity:.4f}"
                            ),
                            backend="hybrid",
                            matched_signals=["embedding_merge"],
                        ),
                        merged,
                    )
        except Exception as exc:
            self.embedding_init_error = str(exc)
            return (
                TopicAssignResult(
                    status="done",
                    topic_id=rule_res.topic_id,
                    topic_title=rule_res.topic_title,
                    similarity=rule_res.similarity,
                    reason=(
                        "backend=hybrid rule_first fallback_to_rule "
                        f"embedding_error: {self.embedding_init_error}"
                    ),
                    backend="hybrid",
                    matched_signals=rule_res.matched_signals,
                ),
                rule_topic,
            )

        # Embedding could not confidently merge; keep rule-created topic.
        return (
            TopicAssignResult(
                status="done",
                topic_id=rule_res.topic_id,
                topic_title=rule_res.topic_title,
                similarity=rule_res.similarity,
                reason=f"backend=hybrid rule_first assigned_by_rule: {rule_res.reason}",
                backend="hybrid",
                matched_signals=rule_res.matched_signals,
            ),
            rule_topic,
        )

    def get_topics(self, task_id: str) -> list[TopicNode]:
        # Rule backend stays authoritative in rule-first hybrid.
        return self.rule_backend.get_topics(task_id)

    def _update_centroid(self, task_id: str, topic_id: str, vector: list[float], message_count: int) -> None:
        old = self._topic_centroids[task_id].get(topic_id)
        if old is None:
            self._topic_centroids[task_id][topic_id] = vector
            return
        count = max(1, message_count)
        merged = [((count - 1) * o + v) / count for o, v in zip(old, vector)]
        norm = sum(x * x for x in merged) ** 0.5
        if norm > 0:
            merged = [x / norm for x in merged]
        self._topic_centroids[task_id][topic_id] = merged
