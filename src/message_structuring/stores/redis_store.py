from __future__ import annotations

import json
import time
from typing import Any

from message_structuring.schemas import AnnotationStatus, NormalizedMessage, SummaryItem, TaskSession, TopicNode

try:
    import redis
except Exception:  # pragma: no cover - optional dependency
    redis = None


class RedisStructuringStore:
    _ALLOWED_COMPONENTS = {"importance", "deliverables", "topic", "summary"}

    def __init__(self, redis_url: str, key_prefix: str = "msl") -> None:
        if redis is None:
            raise RuntimeError("redis package is not installed")
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)
        self.prefix = key_prefix
        self.task_counter_key = self._k("counter:task")
        self.task_ids_key = self._k("task_ids")

    def _k(self, suffix: str) -> str:
        return f"{self.prefix}:{suffix}"

    def _chat_active_task_key(self, chat_id: str) -> str:
        return self._k(f"chat:{chat_id}:active_task")

    def _task_session_key(self, task_id: str) -> str:
        return self._k(f"task:{task_id}:session")

    def _task_message_ids_key(self, task_id: str) -> str:
        return self._k(f"task:{task_id}:message_ids")

    def _task_message_key(self, task_id: str, message_id: str) -> str:
        return self._k(f"task:{task_id}:message:{message_id}")

    def _task_topics_key(self, task_id: str) -> str:
        return self._k(f"task:{task_id}:topics")

    def _task_summary_key(self, task_id: str) -> str:
        return self._k(f"task:{task_id}:summary")

    def _dedup_key(self, dedup_key: str) -> str:
        return self._k(f"dedup:{dedup_key}")

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _load_task(self, task_id: str) -> TaskSession | None:
        raw = self.client.get(self._task_session_key(task_id))
        if not raw:
            return None
        return TaskSession.model_validate_json(raw)

    def _save_task(self, task: TaskSession) -> None:
        self.client.set(self._task_session_key(task.task_id), task.model_dump_json())
        self.client.sadd(self.task_ids_key, task.task_id)

    def start_task(self, chat_id: str, activation_source: str = "manual_api", task_title: str | None = None) -> TaskSession:
        next_id = int(self.client.incr(self.task_counter_key))
        task = TaskSession(
            task_id=f"task_{next_id:06d}",
            display_name=f"task {next_id}",
            status="collecting",
            source_chat_id=chat_id,
            activation_source=activation_source,
            task_title=task_title,
            started_at_ms=self._now_ms(),
        )
        self._save_task(task)
        self.client.set(self._chat_active_task_key(chat_id), task.task_id)
        return task

    def stop_task(self, task_id: str, reason: str = "manual") -> TaskSession | None:
        task = self._load_task(task_id)
        if task is None:
            return None
        task.status = "stopped"
        task.stop_reason = reason
        task.stopped_at_ms = self._now_ms()
        self._save_task(task)
        self.client.delete(self._chat_active_task_key(task.source_chat_id))
        return task

    def get_task(self, task_id: str) -> TaskSession | None:
        return self._load_task(task_id)

    def list_tasks(self) -> list[TaskSession]:
        tasks: list[TaskSession] = []
        for task_id in sorted(self.client.smembers(self.task_ids_key)):
            task = self._load_task(task_id)
            if task is not None:
                tasks.append(task)
        return sorted(tasks, key=lambda t: t.started_at_ms)

    def get_active_task(self, chat_id: str) -> TaskSession | None:
        task_id = self.client.get(self._chat_active_task_key(chat_id))
        if not task_id:
            return None
        task = self._load_task(task_id)
        if task is None or task.status != "collecting":
            return None
        return task

    def append_message(self, task_id: str, message: NormalizedMessage) -> NormalizedMessage:
        message_key = self._task_message_key(task_id, message.message_id)
        dedup_token_key = self._dedup_key(message.dedup.dedup_key)

        with self.client.pipeline() as pipe:
            pipe.set(dedup_token_key, "1", nx=True)
            pipe.exists(message_key)
            dedup_set, msg_exists = pipe.execute()
        if not dedup_set or msg_exists:
            message.dedup.is_duplicate = True
            return message

        self.client.set(message_key, message.model_dump_json())
        self.client.rpush(self._task_message_ids_key(task_id), message.message_id)
        return message

    def get_message(self, task_id: str, message_id: str) -> NormalizedMessage | None:
        raw = self.client.get(self._task_message_key(task_id, message_id))
        if not raw:
            return None
        return NormalizedMessage.model_validate_json(raw)

    def get_messages(self, task_id: str) -> list[NormalizedMessage]:
        ids = self.client.lrange(self._task_message_ids_key(task_id), 0, -1)
        messages: list[NormalizedMessage] = []
        for mid in ids:
            raw = self.client.get(self._task_message_key(task_id, mid))
            if raw:
                messages.append(NormalizedMessage.model_validate_json(raw))
        deduped_by_id = {m.message_id: m for m in messages}
        return sorted(deduped_by_id.values(), key=lambda msg: msg.timestamp_ms)

    def update_annotation(self, task_id: str, message_id: str, component_name: str, annotation: Any) -> NormalizedMessage:
        if component_name not in self._ALLOWED_COMPONENTS:
            raise ValueError(f"Unsupported component namespace: {component_name}")
        msg = self.get_message(task_id, message_id)
        if msg is None:
            raise KeyError(f"message not found: {task_id}/{message_id}")
        setattr(msg.annotations, component_name, annotation)
        self.client.set(self._task_message_key(task_id, message_id), msg.model_dump_json())
        return msg

    def get_processed_messages(self, task_id: str) -> list[NormalizedMessage]:
        processed: list[NormalizedMessage] = []
        for message in self.get_messages(task_id):
            statuses = [
                message.annotations.importance.status,
                message.annotations.deliverables.status,
                message.annotations.topic.status,
            ]
            if all(status in {AnnotationStatus.DONE, AnnotationStatus.ERROR, AnnotationStatus.SKIPPED} for status in statuses):
                processed.append(message)
        return processed

    def set_topics(self, task_id: str, topics: list[TopicNode]) -> None:
        payload = json.dumps([topic.model_dump(mode="json") for topic in topics], ensure_ascii=False)
        self.client.set(self._task_topics_key(task_id), payload)

    def get_topics(self, task_id: str) -> list[TopicNode]:
        raw = self.client.get(self._task_topics_key(task_id))
        if not raw:
            return []
        data = json.loads(raw)
        return [TopicNode.model_validate(item) for item in data]

    def clear_summary_items(self, task_id: str) -> None:
        self.client.set(self._task_summary_key(task_id), "[]")

    def add_summary_item(self, task_id: str, item: SummaryItem) -> bool:
        current = self.get_summary_items(task_id)
        if any(existing.summary_item_id == item.summary_item_id for existing in current):
            return False
        current.append(item)
        payload = json.dumps([summary.model_dump(mode="json") for summary in current], ensure_ascii=False)
        self.client.set(self._task_summary_key(task_id), payload)
        return True

    def get_summary_items(self, task_id: str) -> list[SummaryItem]:
        raw = self.client.get(self._task_summary_key(task_id))
        if not raw:
            return []
        data = json.loads(raw)
        return [SummaryItem.model_validate(item) for item in data]
