from __future__ import annotations

import json
from pathlib import Path


def build_event(
    *,
    idx: int,
    chat_id: str,
    message_id: str,
    text: str | None = None,
    message_type: str = "text",
    thread_id: str | None = None,
    root_id: str | None = None,
    mentions: list[dict] | None = None,
    content_obj: dict | None = None,
) -> dict:
    ts = 1715000000000 + idx * 1000
    if content_obj is None:
        if message_type == "text":
            content_obj = {"text": text or ""}
        elif message_type == "post":
            content_obj = {
                "title": "Auto Post",
                "content": [[{"tag": "text", "text": text or ""}]],
            }
        elif message_type == "image":
            content_obj = {"image_key": f"img_topic_eval_{idx:04d}"}
        else:
            content_obj = {"text": text or ""}

    return {
        "schema": "2.0",
        "header": {
            "event_id": f"evt_topic_eval_{idx:04d}",
            "event_type": "im.message.receive_v1",
            "create_time": str(ts),
            "token": "topic-eval-token",
            "app_id": "cli_topic_eval",
            "tenant_key": "tenant_topic_eval",
        },
        "event": {
            "sender": {
                "sender_id": {
                    "union_id": f"on_topic_eval_{idx:04d}",
                    "user_id": f"u_topic_eval_{idx:04d}",
                    "open_id": f"ou_topic_eval_{idx:04d}",
                },
                "sender_type": "user",
                "tenant_key": "tenant_topic_eval",
            },
            "message": {
                "message_id": message_id,
                "root_id": root_id or message_id,
                "parent_id": root_id or message_id,
                "create_time": str(ts + 1),
                "update_time": str(ts + 2),
                "chat_id": chat_id,
                "thread_id": thread_id or f"omt_topic_eval_{idx:04d}",
                "chat_type": "group",
                "message_type": message_type,
                "content": json.dumps(content_obj, ensure_ascii=False),
                "mentions": mentions or [],
            },
        },
    }


def generate_samples() -> list[dict]:
    records: list[dict] = []
    chat_id = "oc_topic_eval_zh_001"
    idx = 1

    def add(
        topic_key: str,
        should_create: bool,
        scenario: str,
        *,
        text: str | None = None,
        message_type: str = "text",
        thread_id: str | None = None,
        root_id: str | None = None,
        content_obj: dict | None = None,
        mentions: list[dict] | None = None,
    ) -> None:
        nonlocal idx
        message_id = f"om_topic_eval_{idx:04d}"
        event = build_event(
            idx=idx,
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            message_type=message_type,
            thread_id=thread_id,
            root_id=root_id,
            content_obj=content_obj,
            mentions=mentions,
        )
        records.append(
            {
                "expected": {
                    "topic_key": topic_key,
                    "should_create_formal_topic": should_create,
                    "scenario": scenario,
                },
                "event": event,
            }
        )
        idx += 1

    login_thread = "omt_topic_eval_login_01"
    login_root = "om_topic_eval_login_root_01"
    login_msgs = [
        "线上登录接口 500 了，用户无法登录",
        "我来处理，先回滚上一版",
        "后续我排查 token 服务",
        "明天下午同步修复结果",
        "登录模块仍有报错，继续排查",
        "先观察 10 分钟，若异常继续回滚",
        "incident update: login service recovered",
        "token service latency spikes, keep investigating",
        "修复补丁已上线，等待验证",
        "用户登录恢复，监控正常",
        "继续关注登录 API error 率",
        "rollback 已完成，准备复盘",
        "明早同步 incident 复盘结论",
        "排查发现网关配置错误",
        "修复配置并重启登录服务",
        "登录故障处理完成，发总结",
    ]
    for msg in login_msgs:
        add("login_incident", True, "login incident", text=msg, thread_id=login_thread, root_id=login_root)

    rag_msgs = [
        "RAG 摘要模块召回率下降，需要检查 embedding 维度",
        "chunk 策略改成 400 tokens 后效果更稳",
        "summary pipeline 需要补充 rerank",
        "检索结果出现偏差，可能是索引延迟",
        "我们先优化 prompt，再观察摘要一致性",
        "RAG module offline eval pass rate 92%",
        "embedding cache miss 太高，需要排查",
        "更新向量库后摘要质量提升",
        "检索链路建议增加 fallback",
        "summary 结果去重逻辑需要修复",
        "RAG 线上实验 AB 数据已收集",
        "下周评审摘要模块改造方案",
    ]
    for i, msg in enumerate(rag_msgs):
        add(
            "rag_summary_module",
            True,
            "RAG summary module",
            text=msg,
            thread_id=f"omt_topic_eval_rag_{i % 3}",
            root_id=f"om_topic_eval_rag_root_{i % 3}",
        )

    api_msgs = [
        "API migration 第一阶段完成，网关路由切到 v2",
        "迁移脚本需要支持灰度回滚",
        "cutover checklist 还缺监控项",
        "api compatibility test 发现字段不一致",
        "迁移后旧接口流量下降到 15%",
        "需要补充 migration rollback runbook",
        "gateway mapping 文档已更新",
        "本周五完成 API migration 收尾",
        "迁移过程中发现认证 header 差异",
        "v1 接口下周准备下线",
        "api migration status: 80% complete",
        "新增回归用例覆盖关键路由",
    ]
    for i, msg in enumerate(api_msgs):
        add(
            "api_migration",
            True,
            "API migration",
            text=msg,
            thread_id=f"omt_topic_eval_api_{i % 2}",
            root_id=f"om_topic_eval_api_root_{i % 2}",
        )

    redis_msgs = [
        "Redis dependency removal 开始执行",
        "先把缓存读逻辑切到内存实现",
        "依赖清单里还有 redis client",
        "remove Redis from startup config",
        "替换缓存后延迟变化可接受",
        "需要补充无 Redis 场景回归测试",
        "dependency cleanup PR 已提交",
        "redis fallback 代码可以删掉",
        "本周完成 Redis 移除验收",
        "cleanup 文档已更新",
    ]
    for msg in redis_msgs:
        add("redis_removal", True, "Redis dependency removal", text=msg, thread_id="omt_topic_eval_redis_01", root_id="om_topic_eval_redis_root_01")

    delivery_msgs = [
        "今天下班前交付 PRD 初稿",
        "PPT 框架已完成，明早补充数据页",
        "请把方案文档同步到项目盘",
        "周五做项目汇报彩排",
        "PRD review comments 已合并",
        "总结文档需要补充风险章节",
        "deliverable list: PRD, PPT, summary",
        "请确认汇报时间和参会人",
        "本周交付物先以文档为主",
        "PPT 终版今晚发出",
    ]
    for msg in delivery_msgs:
        add("prd_ppt_delivery", True, "PRD/PPT delivery", text=msg, thread_id="omt_topic_eval_delivery_01", root_id="om_topic_eval_delivery_root_01")

    meeting_msgs = [
        "明天 15:00 同步修复进展",
        "周三早上开评审会",
        "本周例会改到周四下午",
        "meeting invite 已经发出",
        "请确认下周站会时间",
        "周会 agenda 已整理",
        "今天 17:30 前给出会议材料",
        "同步会后补充行动项",
        "会议室预定完成",
        "评审会需要产品和研发都参加",
    ]
    for msg in meeting_msgs:
        add("meeting_schedule", True, "meeting schedule", text=msg, thread_id="omt_topic_eval_meeting_01", root_id="om_topic_eval_meeting_root_01")

    noise_msgs = [
        "收到",
        "好的",
        "ok",
        "嗯嗯",
        "哈哈哈",
        "@Tom hello",
        "好的好的",
        "roger",
        "ok",
        "收到",
        "哈哈",
        "嗯嗯",
        "好的",
        "ok",
        "收到",
        "@Tom hello",
    ]
    for i, msg in enumerate(noise_msgs):
        mentions = None
        if msg.startswith("@Tom"):
            mentions = [
                {
                    "key": "@_user_1",
                    "id": {"union_id": "on_tom", "user_id": "u_tom", "open_id": "ou_tom"},
                    "mentioned_type": "user",
                    "name": "Tom",
                    "tenant_key": "tenant_topic_eval",
                }
            ]
            msg = "@_user_1 hello"
        add("noise", False, "noise messages", text=msg, mentions=mentions, thread_id=f"omt_topic_eval_noise_{i % 3}", root_id=f"om_topic_eval_noise_root_{i % 3}")

    for i in range(8):
        add(
            "image_noise",
            False,
            "image messages",
            message_type="image",
            content_obj={"image_key": f"img_topic_eval_noise_{i:03d}"},
            thread_id=f"omt_topic_eval_image_{i % 2}",
            root_id=f"om_topic_eval_image_root_{i % 2}",
        )

    interleaved = [
        ("login_incident", True, "interleaved multi-topic discussion", "登录接口仍有 500，继续盯监控"),
        ("rag_summary_module", True, "interleaved multi-topic discussion", "RAG rerank 参数需要回调"),
        ("meeting_schedule", True, "interleaved multi-topic discussion", "明天下午 3 点同步所有模块状态"),
        ("noise", False, "interleaved multi-topic discussion", "收到"),
        ("api_migration", True, "interleaved multi-topic discussion", "API migration 需要补一个回滚步骤"),
        ("redis_removal", True, "interleaved multi-topic discussion", "Redis 依赖删除后本地启动正常"),
        ("login_incident", True, "interleaved multi-topic discussion", "我来处理登录告警"),
        ("noise", False, "interleaved multi-topic discussion", "ok"),
        ("rag_summary_module", True, "interleaved multi-topic discussion", "embedding 索引重建完成"),
        ("meeting_schedule", True, "interleaved multi-topic discussion", "周会改到周四 16:00"),
        ("api_migration", True, "interleaved multi-topic discussion", "gateway 配置迁移完成"),
        ("prd_ppt_delivery", True, "interleaved multi-topic discussion", "PPT 明天中午前发终版"),
        ("noise", False, "interleaved multi-topic discussion", "哈哈哈"),
        ("login_incident", True, "interleaved multi-topic discussion", "后续排查 token 服务调用链"),
        ("redis_removal", True, "interleaved multi-topic discussion", "cleanup PR 合并后去掉 Redis client"),
        ("meeting_schedule", True, "interleaved multi-topic discussion", "请确认评审会参会名单"),
    ]
    for i, (topic_key, should_create, scenario, msg) in enumerate(interleaved):
        add(
            topic_key,
            should_create,
            scenario,
            text=msg,
            thread_id=f"omt_topic_eval_interleave_{i % 4}",
            root_id=f"om_topic_eval_interleave_root_{i % 4}",
        )

    action_replies = [
        "我来处理",
        "先回滚",
        "继续排查",
        "明天同步",
        "我来跟进",
        "先修复",
        "我负责推进",
        "继续观察",
        "同步结果",
        "排查中",
        "回滚完成",
        "明天给结论",
    ]
    for msg in action_replies:
        add(
            "login_incident",
            True,
            "short action replies",
            text=msg,
            thread_id=login_thread,
            root_id=login_root,
        )

    return records


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    out_dir = root / "data" / "fixtures" / "topic_eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "topic_eval_zh.jsonl"

    records = generate_samples()
    with out_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Generated {len(records)} topic-eval samples at {out_path}")


if __name__ == "__main__":
    main()
