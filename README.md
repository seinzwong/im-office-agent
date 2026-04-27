# im-office-agent

**Agent-Pilot 架构**：本仓库为 **应用内 H5 前端** + **FastAPI Gateway**；**Bot** 在飞书侧配置、**转换 Agents** 为独立服务。详见 [`.cursor/plans/`](.cursor/plans/)（不修改该文件时以代码与 `docs/` 为准）。

## 技术栈

- **Gateway**：Python + [FastAPI](https://fastapi.tiangolo.com/) → [`services/gateway/`](services/gateway/)。业务/产物不存本地 DB，**飞书云盘配置目录**为真源（见 [`.env.example`](.env.example)）。
- **前端**：Vite + React + TypeScript → [`apps/web/`](apps/web/)。
- **飞书对接**：Gateway 使用 **`tenant_access_token` + 官方 OpenAPI**（云盘列目录、docx 创建等）；[`packages/lark_cli_driver/`](packages/lark_cli_driver/) 为历史 CLI 封装，当前 Gateway 不再依赖。

## 快速起本地（与 [docs/demo-script.md](docs/demo-script.md) 一致）

1. 终端 A：`mocks/agents` → `uvicorn main:app --port 18080`（或 `AGENTS_M2M_TOKEN=dev-m2m-secret`）。
2. 终端 B：`services/gateway` 虚拟环境、`export DEV_SKIP_LARK=true`、 `export AGENTS_BASE_URL=http://127.0.0.1:18080`，`uvicorn app.main:app --port 8000`。
3. 终端 C：`cd apps/web && npm i && npm run dev`（代理 `/api` 到 8000）。

## 规范与对接

- Agents 单协议：[`specs/agents-protocol/openapi.yaml`](specs/agents-protocol/openapi.yaml)、[docs/agents-protocol.md](docs/agents-protocol.md)
- 三类对接：[`docs/integrations/`](docs/integrations/)、[飞书自建应用清单](docs/feishu-self-built-app.md)
