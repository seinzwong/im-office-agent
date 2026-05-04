# im-office-agent

A Feishu/Lark office assistant prototype with a FastAPI gateway, a web frontend, and external agent services.

## Components

| Path | Purpose |
| --- | --- |
| `services/gateway/` | FastAPI gateway for Feishu/Lark events, frontend APIs, artifact access, and agent calls. |
| `services/agent/` | Agent service package and multi-agent registry example. |
| `apps/web/` | Vite + React frontend. |
| `docs/` | Demo and integration notes. |

## Gateway Quick Start

```bash
pip install -r services/gateway/requirements.txt
uvicorn services.gateway.main:app --reload --host 0.0.0.0 --port 8000
```

Copy `services/gateway/gateway.example.yaml` to `services/gateway/gateway.yaml` for local configuration.

For multiple external agents, copy `services/agent/agents.example.yaml` to `services/agent/agents.yaml` and set this in `gateway.yaml`:

```yaml
agents_registry_path: ../agent/agents.yaml
```

## Key Docs

- Gateway service: [services/gateway/README.md](services/gateway/README.md)
- Agent protocol: [docs/agents-protocol.md](docs/agents-protocol.md)
- Gateway to agents integration: [docs/integrations/gateway-agents.md](docs/integrations/gateway-agents.md)
- Demo script: [docs/demo-script.md](docs/demo-script.md)
