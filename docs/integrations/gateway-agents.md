# Gateway Agents Integration

Gateway delegates AI work to external agent services. Each service implements the same endpoint:

```text
POST {base_url}/v1/invoke
Authorization: Bearer <M2M_TOKEN>
```

## Single Agent Service

Set these values in `services/gateway/gateway.yaml`:

```yaml
agents_base_url: https://your-agents.example.com
agents_m2m_token: dev-m2m-secret
agents_registry_path: ""
```

## Multiple Agent Services

Copy the registry example:

```bash
cp services/agent/agents.example.yaml services/agent/agents.yaml
```

Set this in `services/gateway/gateway.yaml`:

```yaml
agents_registry_path: ../agent/agents.yaml
```

The registry maps protocol actions to agent IDs:

```yaml
routing:
  summary_from_chat: summary
  deliver_whiteboard: whiteboard
  deliver_slides: slides
```

Each `agents.<agent_id>` entry must include `base_url`. `m2m_token` is optional; when omitted, Gateway uses `agents_m2m_token` from `gateway.yaml`.

## Error Mapping

Agents should prefer HTTP 200 with `ok: false` for business errors. Reserve HTTP errors for transport/auth/rate-limit/unavailable cases.

Suggested error codes:

| Code | Meaning |
| --- | --- |
| `INVALID_TIME_WINDOW` | The time hint could not be parsed. |
| `UPSTREAM_LLM` | Model/provider call failed. |
| `PAYLOAD_INVALID` | Request payload is invalid. |
