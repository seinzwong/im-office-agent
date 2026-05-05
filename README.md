# im-office-agent

**Agent-Pilot 架构**：本仓库为 **应用内 H5 前端** + **FastAPI Gateway**；**Bot** 在飞书侧配置、**转换 Agents** 为独立服务。详见 [`.cursor/plans/`](.cursor/plans/)（不修改该文件时以代码与 `docs/` 为准）。

## 技术栈

- **Gateway**：Python + [FastAPI](https://fastapi.tiangolo.com/) → [`services/gateway/`](services/gateway/)。业务/产物不存本地 DB，**飞书云盘配置目录**为真源（见 [`docs/demo-script.md`](docs/demo-script.md)）。
- **前端**：Vite + React + TypeScript → [`apps/web/`](apps/web/)。
- **飞书对接**：Gateway 使用 **`tenant_access_token` + 官方 OpenAPI**（云盘列目录、docx 创建等）；可选在 `gateway.yaml` 开启 **`lark_ws_events_enabled`** 使用 **官方 SDK WebSocket 长连接** 收机器人消息事件（与开放平台「长连接」订阅配套）。[`packages/lark_cli_driver/`](packages/lark_cli_driver/) 为历史 CLI 封装，当前 Gateway 不再依赖。
- **配置**：前后端各自使用 **YAML**（[`gateway.example.yaml`](services/gateway/gateway.example.yaml)、[`apps/web/config.example.yaml`](apps/web/config.example.yaml)）；Gateway 在读取 `gateway.yaml` 后，若某键为空，会按顺序用 **仓库根 `.env`**、**`services/gateway/.env`** 及 **进程环境变量**（如 `LARK_APP_ID`）补齐，避免仅从 `.env` 迁到 YAML 时漏填飞书凭证导致云盘列表为空。
- **外部 Agents**：须单独部署并实现与 [Gateway README — Agents 调用协议](services/gateway/README.md) 一致的 HTTP 约定。在 **`gateway.yaml`** 中配置 `agents_base_url` + `agents_m2m_token`（单实例），或 `agents_registry_path` 指向多实例注册表 YAML（见 [`services/gateway/app/agents/agents.example.yaml`](services/gateway/app/agents/agents.example.yaml)）。

## 启动命令

在仓库根目录打开两个终端，**先起后端再起前端**（前端 dev 代理目标来自 `apps/web/config.yaml` / `config.example.yaml`）。

### 后端（Gateway）

1. 在 [`services/gateway`](services/gateway) 下将 [`gateway.example.yaml`](services/gateway/gateway.example.yaml) 复制为 **`gateway.yaml`**，按其中注释填写飞书、Agents、`dev_skip_lark` 等。
2. 多 Agent 时：将 [`app/agents/agents.example.yaml`](services/gateway/app/agents/agents.example.yaml) 复制为 `app/agents/agents.yaml`，并在 **`gateway.yaml`** 中设置 `agents_registry_path: app/agents/agents.yaml`（与 `agents_base_url` 二选一）。

```bash
conda activate web-312
cd services/gateway
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

（可选）使用项目内 `python -m venv .venv` 亦可，与上式二选一。

### 前端（Vite）

1. 在 [`apps/web`](apps/web) 下将 [`config.example.yaml`](apps/web/config.example.yaml) 复制为 **`config.yaml`**（可选；缺省时使用示例中的默认 dev 端口与 Gateway 代理地址）。
2. 安装依赖并启动：

```bash
cd apps/web
npm install
npm run dev
```

默认开发地址：<http://127.0.0.1:5173>。指定主机/端口请编辑 **`config.yaml`** 中 `dev.host` / `dev.port`，或临时执行：`npm run dev -- --host 127.0.0.1 --port 5173`（后者仅覆盖 CLI 能传的参数，代理目标仍以 YAML 为准）。

## 本地开发提示

- 完整演示流程见 [docs/demo-script.md](docs/demo-script.md)。

## 规范与对接

- Agents 协议：[services/gateway/README.md](services/gateway/README.md)（「Agents 调用协议」）、[docs/agents-protocol.md](docs/agents-protocol.md)（摘要与交叉引用）
- 三类对接：[`docs/integrations/`](docs/integrations/)、[飞书自建应用清单](docs/feishu-self-built-app.md)
