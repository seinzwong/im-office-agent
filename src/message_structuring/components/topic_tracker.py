from __future__ import annotations

from message_structuring.config import MessageStructuringConfig, load_config_from_env
from message_structuring.schemas import AnnotationStatus, NormalizedMessage, TopicAnnotation, TopicNode
from message_structuring.topic_backends import (
    EmbeddingTopicBackend,
    HybridTopicBackend,
    ModelUnavailableError,
    RuleTopicBackend,
)


class TopicTracker:
    def __init__(self, config: MessageStructuringConfig | None = None, threshold: float = 0.34, recent_window: int = 8) -> None:
        self.config = config or load_config_from_env()
        self.threshold = threshold
        self.recent_window = recent_window
        self.backend = self._build_backend()

    def _build_backend(self):
        mode = self.config.topic_backend
        if mode == "embedding":
            return EmbeddingTopicBackend(
                model_path=self.config.topic_embedding_model_path,
                device=self.config.topic_embedding_device,
                assign_threshold=self.config.topic_embedding_assign_threshold,
                uncertain_threshold=self.config.topic_embedding_uncertain_threshold,
                max_length=self.config.topic_embedding_max_length,
            )
        if mode == "hybrid":
            return HybridTopicBackend(config=self.config)
        return RuleTopicBackend(threshold=self.threshold, recent_window=self.recent_window)

    def process(self, message: NormalizedMessage) -> tuple[TopicAnnotation, TopicNode | None]:
        try:
            result, topic_node = self.backend.assign(message)
        except ModelUnavailableError:
            raise
        except Exception as exc:
            return (
                TopicAnnotation(
                    status=AnnotationStatus.ERROR,
                    similarity=None,
                    reason=f"topic backend failed: {exc}",
                ),
                None,
            )

        status = AnnotationStatus.DONE
        if result.status == "skipped":
            status = AnnotationStatus.SKIPPED
        elif result.status == "error":
            status = AnnotationStatus.ERROR

        return (
            TopicAnnotation(
                status=status,
                topic_id=result.topic_id,
                topic_title=result.topic_title,
                similarity=result.similarity,
                reason=result.reason,
            ),
            topic_node,
        )

    def get_topics(self, task_id: str) -> list[TopicNode]:
        return self.backend.get_topics(task_id)
