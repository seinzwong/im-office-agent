from __future__ import annotations

from message_structuring.schemas import AnnotationStatus, NormalizedMessage, SummaryItem, TopicNode
from message_structuring.stores.memory import MemoryStructuringStore


def main() -> None:
    store = MemoryStructuringStore()
    task = store.start_task(chat_id="oc_store_contract_001", activation_source="test")
    assert store.get_active_task("oc_store_contract_001") is not None
    assert store.get_task(task.task_id) is not None

    msg = NormalizedMessage(
        message_id="m1",
        task_id=task.task_id,
        chat_id="oc_store_contract_001",
        timestamp_ms=1,
    )
    msg.dedup.dedup_key = "feishu:m1:1"
    store.append_message(task.task_id, msg)
    assert store.get_message(task.task_id, "m1") is not None
    assert len(store.get_messages(task.task_id)) == 1

    msg2 = NormalizedMessage(
        message_id="m1",
        task_id=task.task_id,
        chat_id="oc_store_contract_001",
        timestamp_ms=2,
    )
    msg2.dedup.dedup_key = "feishu:m1:1"
    dup = store.append_message(task.task_id, msg2)
    assert dup.dedup.is_duplicate is True
    assert len(store.get_messages(task.task_id)) == 1

    loaded = store.get_message(task.task_id, "m1")
    assert loaded is not None
    loaded.annotations.importance.status = AnnotationStatus.DONE
    loaded.annotations.deliverables.status = AnnotationStatus.DONE
    loaded.annotations.topic.status = AnnotationStatus.SKIPPED
    store.update_annotation(task.task_id, "m1", "importance", loaded.annotations.importance)
    store.update_annotation(task.task_id, "m1", "deliverables", loaded.annotations.deliverables)
    store.update_annotation(task.task_id, "m1", "topic", loaded.annotations.topic)
    assert len(store.get_processed_messages(task.task_id)) == 1

    topic = TopicNode(topic_id="t1", topic_title="topic 1")
    store.set_topics(task.task_id, [topic])
    assert len(store.get_topics(task.task_id)) == 1

    item = SummaryItem(summary_item_id="s1", topic_id="t1", text="x", source_message_ids=["m1"], confidence=0.8)
    assert store.add_summary_item(task.task_id, item) is True
    assert store.add_summary_item(task.task_id, item) is False
    assert len(store.get_summary_items(task.task_id)) == 1
    store.clear_summary_items(task.task_id)
    assert len(store.get_summary_items(task.task_id)) == 0

    stopped = store.stop_task(task.task_id, reason="done")
    assert stopped is not None and stopped.status == "stopped"
    assert store.get_active_task("oc_store_contract_001") is None
    print("Store contract test passed.")


if __name__ == "__main__":
    main()
