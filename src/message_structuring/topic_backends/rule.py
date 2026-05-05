from __future__ import annotations

import re
from collections import defaultdict, deque

from message_structuring.schemas import NormalizedMessage, TopicMessageRef, TopicNode
from message_structuring.topic_backends.base import TopicAssignResult


class RuleTopicBackend:
    NOISE_TERMS = {
        "收到",
        "好的",
        "ok",
        "okay",
        "嗯嗯",
        "哈哈哈",
        "哈哈",
        "roger",
        "鏀跺埌",
        "濂界殑",
    }

    ISSUE_TERMS = {
        "incident",
        "error",
        "bug",
        "500",
        "登录",
        "login",
        "故障",
        "异常",
        "报错",
        "回滚",
        "rollback",
        "排查",
        "token",
    }

    ACTION_TERMS = {
        "我来处理",
        "先回滚",
        "明天同步",
        "继续排查",
        "我来",
        "我负责",
        "处理",
        "回滚",
        "排查",
        "同步",
        "跟进",
        "修复",
        "rollback",
        "investigate",
        "鎴戞潵澶勭悊",
        "鍏堝洖婊?",
        "鏄庡ぉ鍚屾",
        "缁х画鎺掓煡",
    }

    KEYWORD_ALIASES: dict[str, tuple[str, ...]] = {
        "login": ("登录", "登陆", "login", "鐧诲綍", "鐧婚檰"),
        "api": ("api", "接口", "gateway", "网关", "鎺ュ彛", "缃戝叧"),
        "incident": ("故障", "异常", "报错", "incident", "error", "500", "鏁呴殰", "寮傚父", "鎶ラ敊"),
        "rollback": ("回滚", "rollback", "鍥炴粴"),
        "token": ("token", "鉴权", "认证", "閴存潈", "璁よ瘉"),
        "fix": ("修复", "恢复", "fix", "hotfix", "淇", "鎭㈠"),
        "rag": ("rag", "检索", "召回", "向量", "embedding", "chunk", "妫€绱?", "鍙洖"),
        "summary": ("summary", "总结", "摘要", "归纳", "鎬荤粨", "鎽樿"),
        "migration": ("迁移", "migration", "cutover", "杩佺Щ"),
        "redis": ("redis", "缓存", "缂撳瓨"),
        "dependency": ("依赖", "dependency", "remove", "移除", "替换", "渚濊禆", "绉婚櫎"),
        "prd": ("prd", "文档", "方案", "鏂囨。", "鏂规"),
        "ppt": ("ppt", "汇报", "姹囨姤"),
        "meeting": ("会议", "meeting", "同步", "周会", "浼氳", "鍛ㄤ細"),
        "schedule": ("明天", "下午", "今天", "本周", "下周", "deadline", "ddl", "鏄庡ぉ", "涓嬪崍"),
        "task": ("任务", "待办", "action", "处理", "跟进", "浠诲姟", "寰呭姙"),
        "release": ("上线", "发布", "release", "涓婄嚎", "鍙戝竷"),
    }

    STOPWORDS = {
        "the",
        "and",
        "this",
        "that",
        "please",
        "tomorrow",
        "today",
        "hello",
        "hi",
        "我",
        "我们",
        "你",
        "你们",
        "他们",
        "然后",
        "后续",
        "继续",
        "一个",
        "一下",
    }

    def __init__(self, threshold: float = 0.34, recent_window: int = 8) -> None:
        self.threshold = threshold
        self.recent_window = recent_window
        self._task_topics: dict[str, dict[str, TopicNode]] = defaultdict(dict)
        self._task_counter: dict[str, int] = defaultdict(int)
        self._task_conversation_map: dict[str, dict[str, str]] = defaultdict(dict)
        self._task_recent_topic_ids: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=self.recent_window))
        self._task_recent_issue_topic: dict[str, str | None] = defaultdict(lambda: None)

    def assign(
        self,
        message: NormalizedMessage,
        task_state: dict | None = None,
        topics: list[TopicNode] | None = None,
        context: dict | None = None,
    ) -> tuple[TopicAssignResult, TopicNode | None]:
        task_id = message.task_id
        text = (message.content.normalized_text or "").strip()
        if not text:
            return (
                TopicAssignResult(
                    status="skipped",
                    topic_id=None,
                    topic_title=None,
                    similarity=0.0,
                    reason="backend=rule empty message skipped",
                    backend="rule",
                    matched_signals=["empty"],
                ),
                None,
            )

        should_skip, skip_reason = self.should_skip_message(message)
        if should_skip:
            return (
                TopicAssignResult(
                    status="skipped",
                    topic_id=None,
                    topic_title=None,
                    similarity=0.0,
                    reason=f"backend=rule {skip_reason}",
                    backend="rule",
                    matched_signals=["skip_policy"],
                ),
                None,
            )

        task_topics = self._task_topics[task_id]
        topic_from_conversation = self._find_topic_from_conversation(task_id, message)
        if topic_from_conversation is not None:
            updated = self._append_ref(topic_from_conversation, message, role="thread_or_root_inherit")
            self._register_activity(task_id, updated.topic_id, message)
            if updated.message_count >= 5:
                self.rename_topic_stub(updated)
            return (
                TopicAssignResult(
                    status="done",
                    topic_id=updated.topic_id,
                    topic_title=updated.topic_title,
                    similarity=0.97,
                    reason="backend=rule inherited by thread/root",
                    backend="rule",
                    matched_signals=["thread_root_inherit"],
                ),
                updated,
            )

        if self._is_action_message(message):
            issue_topic = self._get_recent_issue_topic(task_id)
            if issue_topic is not None:
                updated = self._append_ref(issue_topic, message, role="action_followup")
                self._register_activity(task_id, updated.topic_id, message)
                return (
                    TopicAssignResult(
                        status="done",
                        topic_id=updated.topic_id,
                        topic_title=updated.topic_title,
                        similarity=0.84,
                        reason="backend=rule attached to recent issue topic",
                        backend="rule",
                        matched_signals=["action_followup"],
                    ),
                    updated,
                )

        best_topic: TopicNode | None = None
        best_score = 0.0
        candidate_keywords = set(self._keywords(text))
        for topic in task_topics.values():
            score = self._topic_match_score(message, candidate_keywords, topic)
            if score > best_score:
                best_score = score
                best_topic = topic

        if best_topic and best_score >= self.threshold:
            updated = self._append_ref(best_topic, message, role="supporting")
            self._register_activity(task_id, updated.topic_id, message)
            if updated.message_count >= 5:
                self.rename_topic_stub(updated)
            return (
                TopicAssignResult(
                    status="done",
                    topic_id=updated.topic_id,
                    topic_title=updated.topic_title,
                    similarity=round(best_score, 4),
                    reason="backend=rule assigned to existing topic",
                    backend="rule",
                    matched_signals=["keyword_similarity"],
                ),
                updated,
            )

        new_topic = self._create_topic(task_id, message)
        task_topics[new_topic.topic_id] = new_topic
        self._register_activity(task_id, new_topic.topic_id, message)
        return (
            TopicAssignResult(
                status="done",
                topic_id=new_topic.topic_id,
                topic_title=new_topic.topic_title,
                similarity=round(best_score, 4),
                reason="backend=rule new topic created",
                backend="rule",
                matched_signals=["new_topic"],
            ),
            new_topic,
        )

    def get_topics(self, task_id: str) -> list[TopicNode]:
        return list(self._task_topics.get(task_id, {}).values())

    def reassign_new_topic_to_existing(
        self,
        task_id: str,
        new_topic_id: str,
        target_topic_id: str,
        message: NormalizedMessage,
        role: str = "embedding_merge",
    ) -> TopicNode | None:
        task_topics = self._task_topics.get(task_id, {})
        new_topic = task_topics.get(new_topic_id)
        target_topic = task_topics.get(target_topic_id)
        if new_topic is None or target_topic is None:
            return None

        # Remove this message reference from newly created topic if present.
        removed = False
        for idx in range(len(new_topic.refs) - 1, -1, -1):
            if new_topic.refs[idx].message_id == message.message_id:
                new_topic.refs.pop(idx)
                removed = True
                break
        if removed:
            new_topic.message_count = max(0, new_topic.message_count - 1)
            new_topic.update_count = max(0, new_topic.update_count - 1)

        # Attach message to target topic and register conversation activity.
        updated_target = self._append_ref(target_topic, message, role=role)
        self._register_activity(task_id, updated_target.topic_id, message)

        # Drop empty new topic to avoid fragmentation.
        if new_topic.message_count <= 0 or not new_topic.refs:
            task_topics.pop(new_topic_id, None)
            recent = self._task_recent_topic_ids.get(task_id)
            if recent and new_topic_id in recent:
                recent.remove(new_topic_id)
            if self._task_recent_issue_topic.get(task_id) == new_topic_id:
                self._task_recent_issue_topic[task_id] = updated_target.topic_id

        return updated_target

    def should_skip_message(self, message: NormalizedMessage) -> tuple[bool, str]:
        normalized = (message.content.normalized_text or "").strip()
        plain = (message.content.plain_text or "").strip().lower()
        stripped = re.sub(r"\s+", " ", plain)

        if message.features.has_image and not self._has_structural_signal(normalized):
            return True, "pure image message skipped"
        if not stripped and message.features.has_mention:
            return True, "pure mention skipped"
        if stripped in self.NOISE_TERMS:
            return True, "noise token skipped"
        if re.fullmatch(r"(@\S+\s*)+", normalized):
            return True, "pure mention form skipped"
        if re.fullmatch(r"@\S+\s*(hello|hi|你好|嗨)?", normalized.lower()):
            return True, "mention greeting skipped"
        if len(stripped) <= 4 and not self._has_structural_signal(normalized):
            return True, "short text without structural signal skipped"
        return False, ""

    def has_structural_signal(self, text: str) -> bool:
        return self._has_structural_signal(text)

    def rename_topic_stub(self, topic: TopicNode) -> None:
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
        lower_text = text.lower()
        tokens = set(re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{1,}|[0-9]{3,}|[\u4e00-\u9fff]{2,8}", lower_text))
        normalized_tokens: set[str] = set()
        for token in tokens:
            if token not in self.STOPWORDS:
                normalized_tokens.add(token)
        for canonical, aliases in self.KEYWORD_ALIASES.items():
            if any(alias.lower() in lower_text for alias in aliases):
                normalized_tokens.add(canonical)
        return sorted(normalized_tokens)[:18]

    def _title_from_text(self, text: str) -> str:
        clean = re.sub(r"\s+", " ", text).strip()
        clean = re.sub(r"@\S+", "", clean).strip()
        if not clean:
            return "untitled_topic"
        return clean[:40]

    def _has_structural_signal(self, text: str) -> bool:
        lower = text.lower()
        if any(term in lower for term in ("deadline", "ddl", "risk", "decision", "todo", "action", "issue")):
            return True
        if re.search(r"\b\d{1,2}:\d{2}\b", lower):
            return True
        if any(alias.lower() in lower for aliases in self.KEYWORD_ALIASES.values() for alias in aliases):
            return True
        return False

    def _is_action_message(self, message: NormalizedMessage) -> bool:
        text = (message.content.normalized_text or "").lower()
        if any(term in text for term in self.ACTION_TERMS):
            return True
        plain = (message.content.plain_text or "").strip()
        if len(plain) <= 10 and any(term in text for term in ("处理", "回滚", "排查", "同步", "跟进", "修复", "澶勭悊", "鍥炴粴")):
            return True
        return False

    def _topic_match_score(self, message: NormalizedMessage, candidate_keywords: set[str], topic: TopicNode) -> float:
        topic_kw = set(topic.keywords)
        if not candidate_keywords and not topic_kw:
            return 0.0

        shared = candidate_keywords & topic_kw
        union = candidate_keywords | topic_kw
        jaccard = len(shared) / len(union) if union else 0.0

        substring_hits = 0
        topic_title = topic.topic_title.lower()
        for kw in candidate_keywords:
            if len(kw) < 3:
                continue
            if kw in topic_title or any(kw in existing or existing in kw for existing in topic_kw):
                substring_hits += 1
        substring_score = min(substring_hits * 0.12, 0.36)

        shared_score = min(len(shared) * 0.1, 0.3)
        recent_bonus = 0.0
        recent_topics = list(self._task_recent_topic_ids[message.task_id])
        if recent_topics:
            if topic.topic_id == recent_topics[-1]:
                recent_bonus = 0.16
            elif topic.topic_id in recent_topics:
                recent_bonus = 0.08

        action_bonus = 0.0
        if self._is_action_message(message) and self._topic_has_issue_signal(topic):
            action_bonus = 0.2

        score = jaccard * 0.45 + substring_score + shared_score + recent_bonus + action_bonus
        return min(score, 1.0)

    def _topic_has_issue_signal(self, topic: TopicNode) -> bool:
        joined = " ".join(topic.keywords).lower()
        return any(term in joined for term in self.ISSUE_TERMS)

    def _register_activity(self, task_id: str, topic_id: str, message: NormalizedMessage) -> None:
        topic = self._task_topics[task_id].get(topic_id)
        if topic is None:
            return
        recent = self._task_recent_topic_ids[task_id]
        if topic_id in recent:
            recent.remove(topic_id)
        recent.append(topic_id)
        if self._topic_has_issue_signal(topic) or self._has_issue_signal(message):
            self._task_recent_issue_topic[task_id] = topic_id
        self._bind_conversation_keys(task_id, message, topic_id)

    def _has_issue_signal(self, message: NormalizedMessage) -> bool:
        text = (message.content.normalized_text or "").lower()
        return any(term in text for term in self.ISSUE_TERMS)

    def _bind_conversation_keys(self, task_id: str, message: NormalizedMessage, topic_id: str) -> None:
        conv_map = self._task_conversation_map[task_id]
        for key in self._conversation_keys(message):
            conv_map[key] = topic_id

    def _find_topic_from_conversation(self, task_id: str, message: NormalizedMessage) -> TopicNode | None:
        conv_map = self._task_conversation_map[task_id]
        task_topics = self._task_topics[task_id]
        for key in self._conversation_keys(message):
            mapped_topic_id = conv_map.get(key)
            if mapped_topic_id and mapped_topic_id in task_topics:
                return task_topics[mapped_topic_id]
        return None

    def _conversation_keys(self, message: NormalizedMessage) -> list[str]:
        keys: list[str] = []
        if message.thread_id:
            keys.append(f"thread:{message.thread_id}")
        if message.root_id:
            keys.append(f"root:{message.root_id}")
        if message.parent_id:
            keys.append(f"parent:{message.parent_id}")
        return keys

    def _get_recent_issue_topic(self, task_id: str) -> TopicNode | None:
        topic_id = self._task_recent_issue_topic.get(task_id)
        if not topic_id:
            return None
        return self._task_topics[task_id].get(topic_id)
