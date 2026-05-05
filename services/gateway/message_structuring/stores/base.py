from __future__ import annotations

from typing import Protocol

from services.gateway.message_structuring.schemas import NormalizedMessage, SummaryItem, TaskSession, TopicNode


class StructuringStore(Protocol):
    def start_task(self, chat_id: str, activation_source: str = "manual_api", task_title: str | None = None) -> TaskSession:
        ...

    def stop_task(self, task_id: str, reason: str = "manual") -> TaskSession | None:
        ...

    def get_task(self, task_id: str) -> TaskSession | None:
        ...

    def list_tasks(self) -> list[TaskSession]:
        ...

    def get_active_task(self, chat_id: str) -> TaskSession | None:
        ...

    def append_message(self, task_id: str, message: NormalizedMessage) -> NormalizedMessage:
        ...

    def get_message(self, task_id: str, message_id: str) -> NormalizedMessage | None:
        ...

    def get_messages(self, task_id: str) -> list[NormalizedMessage]:
        ...

    def update_annotation(self, task_id: str, message_id: str, component_name: str, annotation: object) -> NormalizedMessage:
        ...

    def get_processed_messages(self, task_id: str) -> list[NormalizedMessage]:
        ...

    def set_topics(self, task_id: str, topics: list[TopicNode]) -> None:
        ...

    def get_topics(self, task_id: str) -> list[TopicNode]:
        ...

    def clear_summary_items(self, task_id: str) -> None:
        ...

    def add_summary_item(self, task_id: str, item: SummaryItem) -> bool:
        ...

    def get_summary_items(self, task_id: str) -> list[SummaryItem]:
        ...
