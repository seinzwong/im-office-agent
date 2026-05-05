from __future__ import annotations

import json
from collections import Counter

from message_structuring.orchestrator import MessageStructuringOrchestrator


def _make_event(
    idx: int,
    text: str,
    *,
    message_type: str = "text",
    thread_id: str = "omt_topic_policy_01",
    root_id: str = "om_topic_policy_root_01",
    mentions: list[dict] | None = None,
    content_obj: dict | None = None,
) -> dict:
    ts = 1715100000000 + idx * 1000
    if content_obj is None:
        if message_type == "text":
            content_obj = {"text": text}
        elif message_type == "image":
            content_obj = {"image_key": f"img_topic_policy_{idx:03d}"}
        else:
            content_obj = {"text": text}
    return {
        "schema": "2.0",
        "header": {
            "event_id": f"evt_topic_policy_{idx:03d}",
            "event_type": "im.message.receive_v1",
            "create_time": str(ts),
            "token": "topic-policy-token",
            "app_id": "cli_topic_policy",
            "tenant_key": "tenant_topic_policy",
        },
        "event": {
            "sender": {
                "sender_id": {
                    "union_id": f"on_topic_policy_{idx:03d}",
                    "user_id": f"u_topic_policy_{idx:03d}",
                    "open_id": f"ou_topic_policy_{idx:03d}",
                },
                "sender_type": "user",
                "tenant_key": "tenant_topic_policy",
            },
            "message": {
                "message_id": f"om_topic_policy_{idx:03d}",
                "root_id": root_id,
                "parent_id": root_id,
                "create_time": str(ts + 1),
                "update_time": str(ts + 2),
                "chat_id": "oc_topic_policy_chat_001",
                "thread_id": thread_id,
                "chat_type": "group",
                "message_type": message_type,
                "content": json.dumps(content_obj, ensure_ascii=False),
                "mentions": mentions or [],
            },
        },
    }


def main() -> None:
    orchestrator = MessageStructuringOrchestrator()
    task = orchestrator.start_task(chat_id="oc_topic_policy_chat_001", activation_source="manual_api")

    login_events = [
        _make_event(1, "线上登录接口 500 了，用户无法登录"),
        _make_event(2, "我来处理，先回滚上一版"),
        _make_event(3, "后续我排查 token 服务"),
        _make_event(4, "明天下午同步修复结果"),
    ]
    for event in login_events:
        out = orchestrator.process_feishu_event(event)
        assert out["status"] == "processed"

    noise_mention = _make_event(
        5,
        "@_user_1 hello",
        mentions=[
            {
                "key": "@_user_1",
                "id": {"union_id": "on_tom", "user_id": "u_tom", "open_id": "ou_tom"},
                "mentioned_type": "user",
                "name": "Tom",
                "tenant_key": "tenant_topic_policy",
            }
        ],
        thread_id="omt_topic_policy_noise_01",
        root_id="om_topic_policy_noise_root_01",
    )
    noise_ok = _make_event(6, "ok", thread_id="omt_topic_policy_noise_01", root_id="om_topic_policy_noise_root_01")
    image_noise = _make_event(
        7,
        "",
        message_type="image",
        thread_id="omt_topic_policy_noise_01",
        root_id="om_topic_policy_noise_root_01",
    )
    for event in [noise_mention, noise_ok, image_noise]:
        out = orchestrator.process_feishu_event(event)
        assert out["status"] == "processed"

    result = orchestrator.get_result(task.task_id)
    messages = result["messages"]
    assert len(messages) == 7, "all messages should stay in raw timeline"

    topic_count = result["quality"]["topic_count"]
    assert topic_count <= 2, f"login incident discussion should merge to <=2 topics, got {topic_count}"

    login_messages = [m for m in messages if m["message_id"] in {f"om_topic_policy_{i:03d}" for i in [1, 2, 3, 4]}]
    login_topic_ids = [m["annotations"]["topic"]["topic_id"] for m in login_messages if m["annotations"]["topic"]["status"] == "done"]
    assert len(login_topic_ids) >= 3
    dominant_count = Counter(login_topic_ids).most_common(1)[0][1]
    assert dominant_count >= 3, "action replies should attach to recent incident topic"

    for msg_id in ["om_topic_policy_005", "om_topic_policy_006", "om_topic_policy_007"]:
        msg = next(m for m in messages if m["message_id"] == msg_id)
        assert msg["annotations"]["topic"]["status"] == "skipped"

    print(json.dumps(result["quality"], ensure_ascii=False, indent=2))
    print("Topic policy test passed.")


if __name__ == "__main__":
    main()
