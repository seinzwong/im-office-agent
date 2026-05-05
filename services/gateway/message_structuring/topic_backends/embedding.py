from __future__ import annotations

import math
import re
from pathlib import Path

from services.gateway.message_structuring.schemas import NormalizedMessage, TopicMessageRef, TopicNode
from services.gateway.message_structuring.topic_backends.base import ModelUnavailableError, TopicAssignResult
from services.gateway.message_structuring.topic_backends.rule import RuleTopicBackend


class EmbeddingTopicBackend:
    def __init__(
        self,
        model_path: str = "models/topic_embedding",
        device: str = "auto",
        assign_threshold: float = 0.68,
        uncertain_threshold: float = 0.58,
        max_length: int = 128,
    ) -> None:
        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise ModelUnavailableError(
                f"Topic embedding model path not found: {self.model_path}. "
                "Run download_topic_embedding_model.py explicitly first."
            )

        self.assign_threshold = assign_threshold
        self.uncertain_threshold = uncertain_threshold
        self.max_length = max_length
        self.rule_policy = RuleTopicBackend()
        self._task_topics: dict[str, dict[str, TopicNode]] = {}
        self._task_counter: dict[str, int] = {}
        self._task_centroids: dict[str, dict[str, list[float]]] = {}

        try:
            import torch
        except Exception as exc:  # pragma: no cover
            raise ModelUnavailableError(f"torch unavailable for embedding backend: {exc}") from exc
        self.torch = torch
        self.device = "cuda" if (device == "auto" and torch.cuda.is_available()) else ("cpu" if device == "auto" else device)
        if self.device == "cuda" and not torch.cuda.is_available():
            self.device = "cpu"

        self.mode = ""
        self.st_model = None
        self.tokenizer = None
        self.model = None
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self.st_model = SentenceTransformer(str(self.model_path), device=self.device)
            self.mode = "sentence_transformers"
        except Exception:
            try:
                from transformers import AutoModel, AutoTokenizer

                self.tokenizer = AutoTokenizer.from_pretrained(str(self.model_path), local_files_only=True)
                self.model = AutoModel.from_pretrained(str(self.model_path), local_files_only=True)
                self.model.to(self.device)
                self.model.eval()
                self.mode = "transformers"
            except Exception as exc:
                raise ModelUnavailableError(
                    f"Unable to load topic embedding model from {self.model_path}: {exc}"
                ) from exc

    def assign(
        self,
        message: NormalizedMessage,
        task_state: dict | None = None,
        topics: list[TopicNode] | None = None,
        context: dict | None = None,
    ) -> tuple[TopicAssignResult, TopicNode | None]:
        task_id = message.task_id
        text = (message.content.normalized_text or "").strip() or (message.content.plain_text or "").strip()
        if not text:
            return (
                TopicAssignResult(
                    status="skipped",
                    topic_id=None,
                    topic_title=None,
                    similarity=0.0,
                    reason="backend=embedding empty message skipped",
                    backend="embedding",
                    matched_signals=["empty"],
                ),
                None,
            )

        should_skip, skip_reason = self.rule_policy.should_skip_message(message)
        if should_skip:
            return (
                TopicAssignResult(
                    status="skipped",
                    topic_id=None,
                    topic_title=None,
                    similarity=0.0,
                    reason=f"backend=embedding {skip_reason}",
                    backend="embedding",
                    matched_signals=["skip_policy"],
                ),
                None,
            )

        task_topics = self._task_topics.setdefault(task_id, {})
        task_centroids = self._task_centroids.setdefault(task_id, {})
        vector = self._encode(text)

        best_topic: TopicNode | None = None
        best_similarity = -1.0
        for topic in task_topics.values():
            centroid = task_centroids.get(topic.topic_id)
            if centroid is None:
                centroid = self._encode(topic.topic_title)
                task_centroids[topic.topic_id] = centroid
            sim = self._cosine(vector, centroid)
            if sim > best_similarity:
                best_similarity = sim
                best_topic = topic

        if best_topic is not None and best_similarity >= self.assign_threshold:
            updated = self._append_ref(best_topic, message, role="embedding_assign")
            self._update_centroid(task_id, updated.topic_id, vector)
            return (
                TopicAssignResult(
                    status="done",
                    topic_id=updated.topic_id,
                    topic_title=updated.topic_title,
                    similarity=round(best_similarity, 4),
                    reason="backend=embedding assigned by cosine similarity",
                    backend="embedding",
                    matched_signals=["embedding_similarity"],
                ),
                updated,
            )

        if best_topic is not None and best_similarity >= self.uncertain_threshold and self.rule_policy.has_structural_signal(text):
            updated = self._append_ref(best_topic, message, role="embedding_uncertain_assign")
            self._update_centroid(task_id, updated.topic_id, vector)
            return (
                TopicAssignResult(
                    status="done",
                    topic_id=updated.topic_id,
                    topic_title=updated.topic_title,
                    similarity=round(best_similarity, 4),
                    reason="backend=embedding uncertain threshold assignment",
                    backend="embedding",
                    matched_signals=["embedding_uncertain"],
                ),
                updated,
            )

        if not self.rule_policy.has_structural_signal(text):
            return (
                TopicAssignResult(
                    status="skipped",
                    topic_id=None,
                    topic_title=None,
                    similarity=round(max(best_similarity, 0.0), 4) if best_similarity >= 0 else 0.0,
                    reason="backend=embedding low-structure message skipped",
                    backend="embedding",
                    matched_signals=["no_structural_signal"],
                ),
                None,
            )

        created = self._create_topic(task_id, message)
        task_topics[created.topic_id] = created
        self._task_centroids[task_id][created.topic_id] = vector
        return (
            TopicAssignResult(
                status="done",
                topic_id=created.topic_id,
                topic_title=created.topic_title,
                similarity=round(max(best_similarity, 0.0), 4) if best_similarity >= 0 else 0.0,
                reason="backend=embedding new topic created",
                backend="embedding",
                matched_signals=["new_topic"],
            ),
            created,
        )

    def get_topics(self, task_id: str) -> list[TopicNode]:
        return list(self._task_topics.get(task_id, {}).values())

    def _create_topic(self, task_id: str, message: NormalizedMessage) -> TopicNode:
        self._task_counter[task_id] = self._task_counter.get(task_id, 0) + 1
        topic_num = self._task_counter[task_id]
        topic = TopicNode(
            topic_id=f"{task_id}_topic_{topic_num:03d}",
            topic_title=self._title_from_text(message.content.normalized_text or message.content.plain_text),
            keywords=self.rule_policy._keywords(message.content.normalized_text or message.content.plain_text),
        )
        return self._append_ref(topic, message, role="core")

    def _append_ref(self, topic: TopicNode, message: NormalizedMessage, role: str) -> TopicNode:
        topic.refs.append(
            TopicMessageRef(
                message_id=message.message_id,
                structured_sentence=message.content.normalized_text,
                role=role,
            )
        )
        topic.message_count += 1
        topic.update_count += 1
        topic.keywords = sorted(
            set(topic.keywords + self.rule_policy._keywords(message.content.normalized_text or message.content.plain_text))
        )
        return topic

    def _title_from_text(self, text: str) -> str:
        clean = re.sub(r"\s+", " ", text).strip()
        clean = re.sub(r"@\S+", "", clean).strip()
        if not clean:
            return "untitled_topic"
        return clean[:40]

    def _encode(self, text: str) -> list[float]:
        if self.mode == "sentence_transformers":
            emb = self.st_model.encode(text, normalize_embeddings=True)  # type: ignore[union-attr]
            return [float(x) for x in emb.tolist()]

        assert self.mode == "transformers"
        tok = self.tokenizer(  # type: ignore[operator]
            text,
            truncation=True,
            padding="max_length",
            max_length=self.max_length,
            return_tensors="pt",
        )
        tok = {k: v.to(self.device) for k, v in tok.items()}
        with self.torch.no_grad():
            out = self.model(**tok)  # type: ignore[operator]
            hidden = out.last_hidden_state
            mask = tok["attention_mask"].unsqueeze(-1)
            summed = (hidden * mask).sum(dim=1)
            denom = mask.sum(dim=1).clamp(min=1)
            pooled = summed / denom
            vec = pooled[0]
            norm = self.torch.norm(vec, p=2).clamp(min=1e-12)
            vec = vec / norm
            return [float(x) for x in vec.cpu().tolist()]

    def _cosine(self, a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na <= 0 or nb <= 0:
            return 0.0
        return max(-1.0, min(1.0, dot / (na * nb)))

    def _update_centroid(self, task_id: str, topic_id: str, vector: list[float]) -> None:
        old = self._task_centroids[task_id].get(topic_id)
        topic = self._task_topics.get(task_id, {}).get(topic_id)
        if old is None or topic is None:
            self._task_centroids[task_id][topic_id] = vector
            return
        count = max(1, topic.message_count)
        merged = [((count - 1) * o + v) / count for o, v in zip(old, vector)]
        norm = math.sqrt(sum(x * x for x in merged))
        if norm > 0:
            merged = [x / norm for x in merged]
        self._task_centroids[task_id][topic_id] = merged
