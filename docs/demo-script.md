# Demo Script

## Setup

1. Copy `services/gateway/gateway.example.yaml` to `services/gateway/gateway.yaml`.
2. Set `dev_skip_lark: true` for local development without Feishu/Lark writes.
3. Configure either `agents_base_url` or `agents_registry_path`.
4. Start the gateway:

```bash
pip install -r services/gateway/requirements.txt
uvicorn services.gateway.main:app --reload --host 0.0.0.0 --port 8000
```

## Smoke Tests

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Dev auth:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/dev
```

Feishu/Lark callback challenge:

```bash
curl -X POST http://127.0.0.1:8000/lark/events \
  -H "Content-Type: application/json" \
  -d '{"challenge":"test123"}'
```

Expected response:

```json
{"challenge":"test123"}
```

Trigger a synthetic summary:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/dev/trigger-summary \
  -H "Content-Type: application/json" \
  -d '{"chat_id":"oc_dev","aggregate_text":"Summarize the project discussion."}'
```
