# Gateway Service

Gateway is the FastAPI boundary service for Feishu/Lark events, frontend API calls, artifact listing, and external agent invocation.

## Configuration

Copy `gateway.example.yaml` to `gateway.yaml` and edit local values. Do not commit real secrets.

Settings are loaded in this order:

1. `services/gateway/gateway.yaml`, falling back to `gateway.example.yaml`.
2. Empty YAML values filled from repository `.env` and `services/gateway/.env`.
3. Process environment variables override all previous values.

Common variables:

| Key | Purpose |
| --- | --- |
| `gateway_public_base_url` | Public gateway URL used by callbacks and OAuth. |
| `artifacts_drive_folder_token` | Feishu/Lark Drive folder used for generated artifacts. |
| `dev_skip_lark` | Use placeholder artifacts and skip Feishu writes in development. |
| `agents_base_url` | Single external agent service base URL. |
| `agents_registry_path` | Multi-agent registry path, for example `../agent/agents.yaml`. |
| `agents_m2m_token` | Default machine-to-machine bearer token for agent calls. |
| `lark_app_id` / `lark_app_secret` | Feishu/Lark app credentials. |
| `session_secret` | Session cookie signing secret. Replace in production. |

## Local Development

From the repository root:

```bash
pip install -r services/gateway/requirements.txt
uvicorn services.gateway.main:app --reload --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

## Routes

| Route | Purpose |
| --- | --- |
| `GET /health` | Service health check. |
| `/api/v1/*` | Frontend API, auth helpers, artifact list, delivery trigger. |
| `POST /lark/events` | Feishu/Lark event callback. |

## Agent Protocol

Gateway calls external agent services over HTTPS JSON.

| Item | Value |
| --- | --- |
| URL | `POST {base_url}/v1/invoke` |
| Auth | `Authorization: Bearer <M2M_TOKEN>` |
| Timeout | Gateway client timeout is currently 120 seconds. |

Actions routed by the gateway:

| Action | Purpose |
| --- | --- |
| `summary_from_chat` | Convert chat text into structured document content. |
| `deliver_whiteboard` | Convert document content into whiteboard DSL. |
| `deliver_slides` | Convert document content into slide XML. |

For multiple agent services, copy `services/agent/agents.example.yaml` to `services/agent/agents.yaml` and set `agents_registry_path: ../agent/agents.yaml` in `gateway.yaml`.

## Layout

| Path | Purpose |
| --- | --- |
| `main.py` | FastAPI app factory and app instance. |
| `router.py` | HTTP route aggregation and `/api/v1` handlers. |
| `event_gateway/` | Feishu/Lark event parsing and OpenAPI client code. |
| `raw_timeline/` | Artifact listing and raw timeline source access. |
| `context_hygiene/` | Summary and delivery orchestration. |
| `storage/` | Storage integration placeholders. |
| `schemas/` | Schema placeholders. |
