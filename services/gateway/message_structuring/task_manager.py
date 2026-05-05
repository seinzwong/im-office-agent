from __future__ import annotations

import time
from threading import Lock

from .schemas import TaskSession


def _now_ms() -> int:
    return int(time.time() * 1000)


class InMemoryTaskManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counter = 0
        self._tasks: dict[str, TaskSession] = {}

    def start_task(
        self,
        chat_id: str,
        activation_source: str = "manual_api",
        task_title: str | None = None,
    ) -> TaskSession:
        with self._lock:
            self._counter += 1
            task_id = f"task_{self._counter:06d}"
            task = TaskSession(
                task_id=task_id,
                display_name=f"task {self._counter}",
                status="collecting",
                source_chat_id=chat_id,
                activation_source=activation_source,
                task_title=task_title,
                started_at_ms=_now_ms(),
            )
            self._tasks[task_id] = task
            return task

    def stop_task(self, task_id: str, reason: str = "manual") -> TaskSession | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            task.status = "stopped"
            task.stop_reason = reason
            task.stopped_at_ms = _now_ms()
            return task

    def get_active_task(self, chat_id: str) -> TaskSession | None:
        with self._lock:
            active = [
                task for task in self._tasks.values() if task.source_chat_id == chat_id and task.status == "collecting"
            ]
            if not active:
                return None
            return sorted(active, key=lambda task: task.started_at_ms, reverse=True)[0]

    def get_task(self, task_id: str) -> TaskSession | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self) -> list[TaskSession]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda task: task.started_at_ms)
