# Message Schema (MVP v0.8)

Core normalized object: `NormalizedMessage`.

## Top-level fields
- `message_id`, `event_id`, `task_id`, `chat_id`
- `thread_id`, `root_id`, `parent_id`
- `chat_type`, `source_platform`, `message_type`
- `timestamp_ms`, `update_time_ms`
- `sender`
- `content`
- `mentions`
- `features`
- `dedup`
- `annotations`

## Sender
- `sender_type`, `union_id`, `user_id`, `open_id`, `display_name`

## Content
- `normalized_text`: normalized text with mention resolution.
- `plain_text`: plain text extraction.
- `raw_content`: raw content string from source event.
- `content_parse_status`: `ok | error`.

## Features
- `has_url`, `has_file`, `has_image`, `has_mention`, `at_all`, `text_length`

## Dedup
- `dedup_key`
- `is_duplicate`

## Annotations
- `importance`: score/level/signals from importance backend.
- `deliverables`: rule-based deliverable value boolean + reasons.
- `topic`: topic assignment result.
- `summary`: summary selection result.

`AnnotationStatus` values:
- `pending`
- `running`
- `done`
- `error`
- `skipped`
- `not_selected`

## Task output objects
- `TaskSession`
- `TopicNode` with `refs`
- `SummaryItem`
- `StructuringResult` with `quality` metrics

See implementation for canonical schema:
- `src/message_structuring/schemas.py`
