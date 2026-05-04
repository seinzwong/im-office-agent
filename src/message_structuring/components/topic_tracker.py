from __future__ import annotations

import difflib
import re
from collections import defaultdict

from message_structuring.schemas import AnnotationStatus, NormalizedMessage, TopicAnnotation, TopicMessageRef, TopicNode


class TopicTracker:
    def __init__(self, threshold: float = 0.45) -> None:
        self.threshold = threshold
        self._task_topics: dict[str, dict[str, TopicNode]] = defaultdict(dict)
        self._task_counter: dict[str, int] = defaultdict(int)

    def process(self, message: NormalizedMessage) -> tuple[TopicAnnotation, TopicNode | None]:
        task_id = message.task_id
        text = (message.content.normalized_text or "").strip()
        if not text:
            return (
                TopicAnnotation(
                    status=AnnotationStatus.DONE,
                    topic_id="pending_uncertain",
                    topic_title="pending_uncertain",
                    similarity=0.0,
                    reason="empty message",
                ),
                None,
            )

        weak = self._is_weak_message(message)
        task_topics = self._task_topics[task_id]

        best_topic: TopicNode | None = None
        best_score = 0.0
        for topic in task_topics.values():
            score = self._similarity(text, topic)
            if score > best_score:
                best_score = score
                best_topic = topic

        if best_topic and best_score >= self.threshold:
            updated = self._append_ref(best_topic, message, role="supporting")
            if updated.message_count >= 5:
                self.rename_topic_stub(updated)
            return (
                TopicAnnotation(
                    status=AnnotationStatus.DONE,
                    topic_id=updated.topic_id,
                    topic_title=updated.topic_title,
                    similarity=round(best_score, 4),
                    reason="assigned to existing topic",
                ),
                updated,
            )

        if weak:
            if "misc" not in task_topics:
                task_topics["misc"] = TopicNode(topic_id="misc", topic_title="misc")
            updated = self._append_ref(task_topics["misc"], message, role="weak_signal")
            return (
                TopicAnnotation(
                    status=AnnotationStatus.DONE,
                    topic_id="misc",
                    topic_title="misc",
                    similarity=round(best_score, 4),
                    reason="weak message routed to misc",
                ),
                updated,
            )

        new_topic = self._create_topic(task_id, message)
        task_topics[new_topic.topic_id] = new_topic
        return (
            TopicAnnotation(
                status=AnnotationStatus.DONE,
                topic_id=new_topic.topic_id,
                topic_title=new_topic.topic_title,
                similarity=round(best_score, 4),
                reason="new topic created",
            ),
            new_topic,
        )

    def get_topics(self, task_id: str) -> list[TopicNode]:
        return list(self._task_topics.get(task_id, {}).values())

    def rename_topic_stub(self, topic: TopicNode) -> None:
        if topic.topic_title in {"misc", "pending_uncertain"}:
            return
        first_ref = topic.refs[0].structured_sentence if topic.refs else topic.topic_title
        topic.topic_title = self._title_from_text(first_ref)

    def _create_topic(self, task_id: str, message: NormalizedMessage) -> TopicNode:
        self._task_counter[task_id] += 1
        topic_num = self._task_counter[task_id]
        topic = TopicNode(
            topic_id=f"{task_id}_topic_{topic_num:03d}",
            topic_title=self._title_from_text(message.content.normalized_text),
            keywords=self._keywords(message.content.normalized_text),
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
        topic.keywords = sorted(set(topic.keywords + self._keywords(message.content.normalized_text)))
        return topic

    def _keywords(self, text: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}", text.lower())
        stop = {"the", "and", "this", "that", "我们", "你们", "他们"}
        return [token for token in tokens if token not in stop][:12]

    def _similarity(self, text: str, topic: TopicNode) -> float:
        candidate_kw = set(self._keywords(text))
        topic_kw = set(topic.keywords)
        jaccard = (len(candidate_kw & topic_kw) / len(candidate_kw | topic_kw)) if (candidate_kw or topic_kw) else 0.0
        seq = difflib.SequenceMatcher(a=text.lower(), b=topic.topic_title.lower()).ratio()
        return max(jaccard, seq * 0.8)

    def _title_from_text(self, text: str) -> str:
        clean = re.sub(r"\s+", " ", text).strip()
        if not clean:
            return "pending_uncertain"
        return clean[:40]

    def _is_weak_message(self, message: NormalizedMessage) -> bool:
        plain = (message.content.plain_text or "").strip().lower()
        if len(plain) <= 2:
            return True
        weak_terms = {"ok", "好的", "收到", "哈哈", "鏀跺埌", "濂界殑"}
        if plain in weak_terms:
            return True
        if message.features.has_image and len(plain) < 4:
            return True
        return False
