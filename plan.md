# MessageStructuringLayer Autonomous Implementation Plan

> Goal: let VS Code Codex implement the `MessageStructuringLayer` end-to-end with minimal human intervention.
> Current repo root: `SummaryCandidateSelector/`
> Existing files can stay. Codex should add the new implementation under `src/message_structuring/`.

---

## 0. Operating mode for Codex

### Required Codex permissions

Use one of these two ways.

#### Option A: VS Code UI

1. Open the Codex panel in VS Code.
2. Select model: `5.3-Codex` with reasoning effort `High` if available.
3. Open the permission/mode selector near the Codex input box.
4. Select `Agent (Full Access)` or equivalent.
5. Keep VS Code open and keep the computer awake.

#### Option B: project config

Create this file:

```text
.codex/config.toml
```

Recommended contents:

```toml
# Full autonomous mode.
# Use only in this trusted project directory.
approval_policy = "never"
sandbox_mode = "danger-full-access"

# Allow live web access for package/doc lookup if Codex needs it.
web_search = "live"

# Windows native sandbox setting; only relevant if the extension uses the Windows sandbox.
[windows]
sandbox = "elevated"
```

If the model name in config is supported by your Codex version, you may add it; otherwise select the model from the VS Code UI:

```toml
# Optional. Prefer UI selection if this model id is not recognized.
# model = "gpt-5.3-codex"
# model_reasoning_effort = "high"
```

---

## 1. Safety boundaries even in Full Access

Codex may:

- create, edit, and delete files inside this repo;
- create new directories under `src/`, `tests/`, `data/`, `models/`;
- install missing Python packages into the project conda environment;
- run tests and FastAPI locally;
- read `plan.md`, `messsage_example.json`, and existing source files.

Codex must not:

- edit files outside this repo unless explicitly required;
- read or print secrets, tokens, cookies, SSH keys, browser data, or unrelated user files;
- run `git push`;
- delete `.git`;
- change system-wide Anaconda/base environment;
- install packages into base Python;
- require real Feishu credentials for MVP;
- require cloud LLM/API keys for MVP.

Use this Python executable for all commands:

```powershell
C:\Anaconda3\envs\summary-selector\python.exe
```

Install missing packages only like this:

```powershell
C:\Anaconda3\envs\summary-selector\python.exe -m pip install <package>
```

Do not use plain `python` or plain `pip`.

---

## 2. Current directory status

Current root is acceptable:

```text
SummaryCandidateSelector/
  .vscode/
  data/
  models/
  src/
    scripts/
    selector/
    service/
    train/
  messsage_example.json
  plan.md
  test.py
```

Do not delete old `src/selector`. It contains earlier experiments. Add new code under:

```text
src/message_structuring/
```

Add tests under:

```text
src/scripts/
```

or, if Codex prefers:

```text
tests/
```

---

## 3. Product architecture alignment

This component is not merely `SummaryCandidateSelector`.

Correct module name:

```text
MessageStructuringLayer
```

It receives Feishu group chat events after activation, normalizes and deduplicates messages, appends them to a task raw timeline, runs three parallel components, and produces a structured result for downstream Agent Planner / Artifact generation.

The three parallel components are:

1. `SummaryCandidateSelector`
   - writes `annotations.importance`
   - outputs summary value score in `[0, 1]`

2. `DeliverableExtractor`
   - writes `annotations.deliverables`
   - extracts person, meeting, task, artifact, email, system/module, time/deadline

3. `TopicTracker`
   - writes `annotations.topic`
   - maintains task-level topics incrementally

Parallel write rule:

```text
Message base fields are immutable after append.
Each component may only write its own annotation namespace.
```

---

## 4. Target normalized message schema

Implement with Pydantic v2.

Each normalized message should look roughly like:

```json
{
  "message_id": "om_xxx",
  "event_id": "event_xxx",
  "task_id": "task_000001",
  "chat_id": "oc_xxx",
  "thread_id": "omt_xxx",
  "root_id": "om_xxx",
  "parent_id": "om_xxx",
  "chat_type": "group",
  "source_platform": "feishu",
  "message_type": "text",
  "timestamp_ms": 1609073151345,
  "sender": {
    "sender_type": "user",
    "union_id": "...",
    "user_id": "...",
    "open_id": "...",
    "display_name": null
  },
  "content": {
    "normalized_text": "@Tom hello",
    "plain_text": "hello",
    "raw_content": "{\"text\":\"@_user_1 hello\"}",
    "content_parse_status": "ok"
  },
  "mentions": [],
  "features": {
    "has_url": false,
    "has_file": false,
    "has_image": false,
    "has_mention": true,
    "at_all": false,
    "text_length": 5
  },
  "dedup": {
    "dedup_key": "feishu:om_xxx:1687343654666",
    "is_duplicate": false
  },
  "annotations": {
    "importance": {
      "status": "pending",
      "score": null,
      "level": null,
      "reason": null,
      "matched_signals": []
    },
    "deliverables": {
      "status": "pending",
      "has_deliverable": null,
      "items": []
    },
    "topic": {
      "status": "pending",
      "topic_id": null,
      "topic_title": null,
      "similarity": null
    },
    "summary": {
      "status": "not_selected",
      "summary_item_ids": []
    }
  }
}
```

Do not use `-inf` for unprocessed dimensions. Use status fields:
`pending`, `running`, `done`, `error`, `skipped`, `not_selected`.

---

## 5. Implementation phases

### Phase 1: Schemas

Create:

```text
src/message_structuring/__init__.py
src/message_structuring/schemas.py
```

Implement:

- `AnnotationStatus`
- `SenderInfo`
- `MentionInfo`
- `MessageContent`
- `MessageFeatures`
- `DedupInfo`
- `ImportanceAnnotation`
- `DeliverableItem`
- `DeliverableAnnotation`
- `TopicAnnotation`
- `SummaryAnnotation`
- `MessageAnnotations`
- `NormalizedMessage`
- `TaskSession`
- `TopicMessageRef`
- `TopicNode`
- `SummaryItem`
- `StructuringResult`

Requirements:

- use Pydantic v2;
- JSON serializable;
- defaults should produce pending annotations;
- no business logic in schema file except helper constructors if needed.

Test:

```powershell
$env:PYTHONPATH="src"
C:\Anaconda3\envs\summary-selector\python.exe -c "from message_structuring.schemas import NormalizedMessage; print('schemas ok')"
```

---

### Phase 2: Feishu preprocessor

Create:

```text
src/message_structuring/preprocessor.py
src/scripts/test_preprocessor.py
```

Implement:

```python
parse_feishu_event(raw_event: dict, task_id: str | None = None) -> NormalizedMessage
```

Input: Feishu `im.message.receive_v1` raw JSON, like `messsage_example.json`.

Requirements:

- parse `event.message.content`, which is itself a JSON string;
- support `message_type = text`;
- support `message_type = post`;
- support `message_type = image`;
- support unknown type without crashing;
- for text, extract `content["text"]`;
- for post, extract title and all `tag == "text"` fragments;
- for image, set `normalized_text = "[图片]"` and `has_image = true`;
- replace mention keys such as `@_user_1` with mention name such as `@Tom`;
- keep `plain_text` with mention markers removed where reasonable;
- extract `message_id`, `chat_id`, `thread_id`, `root_id`, `parent_id`, `create_time`, `update_time`, `sender_id`, `mentions`;
- generate `features.has_url`, `features.has_file`, `features.has_image`, `features.has_mention`, `features.at_all`, `features.text_length`;
- generate `dedup_key = source_platform + ":" + message_id + ":" + update_time`;
- never crash on content parse error. Mark `content_parse_status = "error"` and preserve raw content.

Test command:

```powershell
$env:PYTHONPATH="src"
C:\Anaconda3\envs\summary-selector\python.exe src\scripts\test_preprocessor.py
```

Expected: prints a normalized JSON from `messsage_example.json`.

---

### Phase 3: Timeline store

Create:

```text
src/message_structuring/timeline_store.py
```

MVP is in-memory, not Redis.

Implement:

- `append_message(task_id, message)`
- `get_messages(task_id)`
- `get_message(task_id, message_id)`
- `get_pending_messages(task_id, component_name)`
- `update_annotation(task_id, message_id, component_name, annotation)`
- `mark_duplicate(task_id, message_id)`
- `get_processed_messages(task_id)`

Requirements:

- use `threading.Lock`;
- message base fields should not be mutated by components;
- updates are namespace-isolated:
  - `importance`
  - `deliverables`
  - `topic`
  - `summary`
- return messages sorted by `timestamp_ms`.

---

### Phase 4: Task manager

Create:

```text
src/message_structuring/task_manager.py
```

Implement:

- `start_task(chat_id, activation_source="manual_api", task_title=None)`
- `stop_task(task_id, reason="manual")`
- `get_active_task(chat_id)`
- `get_task(task_id)`
- `list_tasks()`

Requirements:

- in-memory MVP;
- task ids: `task_000001`, `task_000002`, ...
- display names: `task 1`, `task 2`, ...
- statuses: `collecting`, `stopped`, `archived`;
- do not depend on frontend or Activation Router yet.

---

### Phase 5: Importance component

Create:

```text
src/message_structuring/components/__init__.py
src/message_structuring/components/importance.py
```

Implement:

```python
class SummaryCandidateSelector:
    def process(self, message: NormalizedMessage) -> ImportanceAnnotation:
        ...
```

MVP: rule-based only.

Requirements:

- output score in `[0, 1]`;
- output level: `noise`, `low`, `medium`, `high`;
- strong signals: issue, action_item, decision, deadline, risk, reference, progress, requirement, solution;
- noise signals: `收到`, `好的`, `ok`, `哈哈`, empty, very short;
- include `reason` and `matched_signals`;
- no BERT in MVP.

---

### Phase 6: Deliverable extractor

Create:

```text
src/message_structuring/components/deliverables.py
```

Implement:

```python
class DeliverableExtractor:
    def process(self, message: NormalizedMessage) -> DeliverableAnnotation:
        ...
```

MVP: regex/rules only. Leave `llm_client=None` hook for later.

Extract labels:

- `person`
- `time`
- `deadline`
- `meeting`
- `artifact`
- `email`
- `system_or_module`
- `task`

Rules:

- mentions become `person`;
- sender can be included only if the text indicates responsibility, e.g. `我来`, `我负责`;
- emails use regex;
- times include `今天`, `明天`, `下周`, `本周`, `月底`, `deadline`, `ddl`;
- artifacts include `文档`, `方案`, `PPT`, `汇报`, `总结`, `PRD`;
- system/modules include `接口`, `服务`, `数据库`, `模型`, `RAG`, `agent`, `Redis`, `BERT`, `embedding`.

---

### Phase 7: Topic tracker

Create:

```text
src/message_structuring/components/topic_tracker.py
```

MVP: no embedding model yet. Use simple keyword/Jaccard/difflib similarity.

Implement:

```python
class TopicTracker:
    def process(self, message: NormalizedMessage) -> tuple[TopicAnnotation, TopicNode | None]:
        ...
```

Requirements:

- maintain task-level topics in memory;
- assign to existing topic if similarity >= threshold;
- create new topic if no close topic and message has structure value;
- use `misc` or `pending_uncertain` for weak messages;
- initial topic title from first meaningful message;
- add `TopicMessageRef` with:
  - message_id
  - structured_sentence
  - role
- when topic message_count reaches 5, call `rename_topic_stub`;
- do not call external LLM in MVP.

---

### Phase 8: Summary updater

Create:

```text
src/message_structuring/summary_updater.py
```

Implement:

```python
class IncrementalSummaryUpdater:
    def maybe_update(message, topics) -> SummaryItem | None:
        ...
```

Trigger if:

- `importance.score >= 0.75`
- or `importance.score >= 0.45 and deliverables.has_deliverable`
- or `len(deliverables.items) >= 3`
- or `importance.matched_signals` contains decision/deadline/action_item/risk
- or topic reaches update count threshold

MVP summary text can be rule-based:

```text
[topic_title] normalized_text
```

Requirements:

- same message should not generate duplicate summary items;
- include source_message_ids;
- include topic_id;
- include confidence;
- write summary annotation.

---

### Phase 9: Orchestrator

Create:

```text
src/message_structuring/orchestrator.py
```

Implement:

```python
class MessageStructuringOrchestrator:
    def process_feishu_event(self, raw_event: dict) -> dict:
        ...
    def start_task(...)
    def stop_task(...)
    def get_result(task_id)
```

Pipeline:

1. find active task for chat_id;
2. if no active task, return ignored;
3. preprocess raw event;
4. append to timeline with dedup;
5. run importance, deliverables, topic in parallel using `ThreadPoolExecutor`;
6. update annotations namespace by namespace;
7. run summary updater when all three components are done;
8. return updated message JSON.

Requirements:

- components can fail independently;
- one component failure should not crash the entire message;
- errors should be recorded in annotation status/reason;
- keep code readable and testable.

---

### Phase 10: FastAPI service

Create/update:

```text
src/service/api.py
```

Endpoints:

- `GET /health`
- `POST /tasks/start`
- `POST /tasks/{task_id}/stop`
- `GET /tasks/{task_id}`
- `GET /tasks/{task_id}/result`
- `POST /events/feishu`

Run command:

```powershell
$env:PYTHONPATH="src"
C:\Anaconda3\envs\summary-selector\python.exe -m uvicorn src.service.api:app --host 127.0.0.1 --port 8000 --reload
```

Test with PowerShell:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/tasks/start -ContentType "application/json" -Body '{"chat_id":"oc_5ce6d572455d361153b7xx51da133945"}'
```

Then post `messsage_example.json` to:

```text
POST /events/feishu
```

---

### Phase 11: End-to-end test script

Create:

```text
src/scripts/test_e2e.py
```

It should:

1. start a task for the chat_id from `messsage_example.json`;
2. process `messsage_example.json`;
3. print updated normalized message;
4. print final result JSON;
5. assert:
   - message exists in timeline;
   - annotations.importance.status is done;
   - annotations.deliverables.status is done;
   - annotations.topic.status is done;
   - no unhandled exception.

Run:

```powershell
$env:PYTHONPATH="src"
C:\Anaconda3\envs\summary-selector\python.exe src\scripts\test_e2e.py
```

---

## 6. Final expected output

At the end, `GET /tasks/{task_id}/result` should return:

```json
{
  "task": {
    "task_id": "task_000001",
    "display_name": "task 1",
    "status": "stopped",
    "source_chat_id": "oc_xxx"
  },
  "task_brief": {
    "summary": "...",
    "goal": "...",
    "deliverables": [],
    "confidence": 0.0
  },
  "summary": {
    "items": []
  },
  "topics": [],
  "messages": [],
  "quality": {
    "total_messages": 0,
    "processed_messages": 0,
    "summary_candidate_count": 0,
    "topic_count": 0,
    "errors": []
  }
}
```

Exact content can differ, but the structure should be stable.

---

## 7. One-shot prompt to paste into Codex

Paste this into VS Code Codex after selecting Full Access:

```text
Read plan.md completely and implement it end-to-end.

You are working in the current VS Code workspace:
C:\Users\Seinz\Desktop\Lark-ai-project\SummaryCandidateSelector

Use this Python executable for all Python and pip commands:
C:\Anaconda3\envs\summary-selector\python.exe

Do not use base Python, plain python, or plain pip.

You have permission to:
- create/edit files in this repo;
- add src/message_structuring;
- install missing Python packages into the summary-selector conda env;
- run tests;
- run FastAPI locally;
- update plan-related implementation files.

Do not:
- delete old src/selector;
- edit files outside this repo;
- read secrets or unrelated user files;
- run git push;
- require real Feishu credentials or cloud LLM keys.

Implement phases 1-11 from plan.md.
After each phase, run the relevant test command.
If a package is missing, install it with:
C:\Anaconda3\envs\summary-selector\python.exe -m pip install <package>

If an optional cloud/API/LLM integration is missing, implement a local stub instead of blocking.
If Redis is not available, use the in-memory implementation specified in plan.md.

When done, provide:
1. changed files;
2. commands run;
3. test results;
4. remaining TODOs.
```

---

## 8. If Codex stops and asks

If Codex still asks for approval despite Full Access:

1. Check the permission selector under the Codex input box.
2. Set it to `Agent (Full Access)`.
3. If available, choose “approve all for this session”.
4. If still blocked, open Codex settings and confirm `.codex/config.toml` or `~/.codex/config.toml` contains:

```toml
approval_policy = "never"
sandbox_mode = "danger-full-access"
```

5. Restart VS Code and start a new Codex thread.

Some managed environments may disallow `approval_policy = "never"` or `sandbox_mode = "danger-full-access"`. In that case use `workspace-write` + `on-request` and approve the first required commands manually.
