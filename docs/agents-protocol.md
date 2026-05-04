# Agents Protocol

Gateway calls external agent services with a stable HTTP JSON protocol.

## Endpoint

| Item | Value |
| --- | --- |
| Method | `POST` |
| URL | `{base_url}/v1/invoke` |
| Auth | `Authorization: Bearer <M2M_TOKEN>` |
| Content type | `application/json` |

`base_url` comes from `agents_base_url` in `services/gateway/gateway.yaml`, or from the multi-agent registry referenced by `agents_registry_path`.

## Request

```json
{
  "protocol_version": 1,
  "request_id": "uuid",
  "idempotency_key": "uuid",
  "trace_id": "trace-id",
  "action": "summary_from_chat",
  "context": {},
  "payload": {}
}
```

## Response

```json
{
  "protocol_version": 1,
  "request_id": "uuid",
  "ok": true,
  "result": {}
}
```

When `ok` is `false`, return:

```json
{
  "protocol_version": 1,
  "request_id": "uuid",
  "ok": false,
  "error": {
    "code": "PAYLOAD_INVALID",
    "message": "Human readable error",
    "details": {}
  }
}
```

## Actions

| Action | Payload | Result |
| --- | --- | --- |
| `summary_from_chat` | `chat_id`, `message_text_aggregated`, `time_hint_text`, `default_window_end_unix` | `time_window`, `doc_title`, `doc_content_xml` |
| `deliver_whiteboard` | `document_id`, `body_plain` | `whiteboard_dsl`, optional `notes` |
| `deliver_slides` | `document_id`, `body_plain` | `slide_xml_slides`, optional `notes` |

## Registry

For multiple agent services, copy `services/agent/agents.example.yaml` to `services/agent/agents.yaml` and set `agents_registry_path: ../agent/agents.yaml` in `services/gateway/gateway.yaml`.
