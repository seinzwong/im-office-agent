# im-office-agent

**Agent-Pilot 架构**：本仓库为 **应用内 H5 前端** + **FastAPI Gateway**；**Bot** 在飞书侧配置、**转换 Agents** 为独立服务。详见 [`.cursor/plans/`](.cursor/plans/)（不修改该文件时以代码与 `docs/` 为准）。

## 技术栈

- **Gateway**：Python + [FastAPI](https://fastapi.tiangolo.com/) → [`services/gateway/`](services/gateway/)。业务/产物不存本地 DB，**飞书云盘配置目录**为真源（见 [`docs/demo-script.md`](docs/demo-script.md)）。
- **前端**：Vite + React + TypeScript → [`apps/web/`](apps/web/)。
- **飞书对接**：Gateway 使用 **`tenant_access_token` + 官方 OpenAPI**（云盘列目录、docx 创建等）；[`packages/lark_cli_driver/`](packages/lark_cli_driver/) 为历史 CLI 封装，当前 Gateway 不再依赖。
- **外部 Agents**：须单独部署并实现 [`specs/agents-protocol/openapi.yaml`](specs/agents-protocol/openapi.yaml)。**单实例**：配置 `AGENTS_BASE_URL` + `AGENTS_M2M_TOKEN`。**多实例**：使用 [`services/gateway/config/agents.example.yaml`](services/gateway/config/agents.example.yaml) 复制为自有 YAML，设 `AGENTS_REGISTRY_PATH`（相对路径相对 `services/gateway`），按 `action` 路由到不同基址。

## 快速起本地（与 [docs/demo-script.md](docs/demo-script.md) 一致）

1. 准备可用的 **外部 Agents**（实现 `POST /v1/invoke`），记下基址与 M2M 密钥；多实例时准备注册表 YAML。
2. 终端 A：`services/gateway` 虚拟环境，`export DEV_SKIP_LARK=true`，任选其一：
   - 单 URL：`export AGENTS_BASE_URL=<基址>`、`export AGENTS_M2M_TOKEN=<密钥>`；
   - 多 URL：`export AGENTS_REGISTRY_PATH=config/agents.yaml`（并保证 `routing` 覆盖三个 `action`），`AGENTS_M2M_TOKEN` 作为各 Agent 未单独写 `m2m_token` 时的默认鉴权。
   - 然后：`uvicorn app.main:app --port 8000`。
3. 终端 B：`cd apps/web && npm i && npm run dev`（代理 `/api` 到 8000）。

## 规范与对接

- Agents 单协议：[`specs/agents-protocol/openapi.yaml`](specs/agents-protocol/openapi.yaml)、[docs/agents-protocol.md](docs/agents-protocol.md)
- 三类对接：[`docs/integrations/`](docs/integrations/)、[飞书自建应用清单](docs/feishu-self-built-app.md)
