from selector.schemas import (
    CandidateScore,
    ChatMessage,
    MessageContext,
    SelectorAction,
    SummaryType,
)


def main():
    msg1 = ChatMessage(
        message_id="msg_001",
        group_id="dev_group",
        sender_id="u_001",
        sender_name="张三",
        text="线上登录接口 500 了，用户现在无法登录",
        has_url=False,
        has_file=False,
    )

    msg2 = ChatMessage(
        message_id="msg_002",
        group_id="dev_group",
        sender_id="u_002",
        sender_name="李四",
        text="我来处理，先回滚上一版",
        reply_to_id="msg_001",
    )

    context = MessageContext(
        current_message=msg2,
        previous_messages=[msg1],
    )

    score = CandidateScore(
        message_id=msg2.message_id,
        group_id=msg2.group_id,
        score=92.0,
        level=3,
        should_summarize=True,
        action=SelectorAction.ENTER_SUMMARY_QUEUE,
        summary_type=SummaryType.ACTION_ITEM,
        rule_score=92.0,
        reason="包含线上问题、负责人、处理方案",
        matched_keywords=["线上", "我来", "回滚"],
    )

    print("MessageContext:")
    print(context.model_dump_json(indent=2, ensure_ascii=False))

    print("\nCandidateScore:")
    print(score.model_dump_json(indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()