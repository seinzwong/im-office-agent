# API Contract (MVP v0.8)

Base URL: `http://127.0.0.1:8000`

## Health
- `GET /health`
- Response:
```json
{ "status": "ok" }
```

## Task lifecycle
- `POST /tasks/start`
  - Body:
```json
{ "chat_id": "oc_xxx", "activation_source": "manual_api", "task_title": "optional" }
```
- `POST /tasks/{task_id}/stop`
  - Body:
```json
{ "reason": "manual" }
```
- `GET /tasks/{task_id}`

## Activation
- `POST /activation/state`
  - Body:
```json
{ "chat_id": "oc_xxx", "active": true, "task_title": "optional" }
```

## Event ingestion
- `POST /events/feishu`
  - Body: single raw Feishu `im.message.receive_v1` style event.
- `POST /events/feishu/batch`
  - Body:
```json
{ "events": [ { "schema": "2.0", "header": {}, "event": {} } ] }
```

## Task reads
- `GET /tasks/{task_id}/messages`
- `GET /tasks/{task_id}/topics`
- `GET /tasks/{task_id}/summary`
- `GET /tasks/{task_id}/result`

## Summary reprocess
- `POST /tasks/{task_id}/reprocess/summary`

## Runtime debug
- `GET /debug/config`
- Returns runtime mode, model paths, and threshold values.
- API key is not returned directly; only `teammate_summary_api_key_configured: true/false`.

## Error behavior
- Unknown task on task-specific getters returns HTTP 404.
- Event without `chat_id` returns ignored response.
- Event when no active task is buffered/ignored until task activation.
