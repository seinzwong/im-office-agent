# 演示脚本（赛题/内部验收）

## 准备

1. 终端 A：Gateway（开发跳过飞书写盘；**须**指向已部署的外部 Agents）  
   - 单实例：`cd services/gateway && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && export DEV_SKIP_LARK=true AGENTS_BASE_URL=<你的 Agents 基址> AGENTS_M2M_TOKEN=<与 Agents 服务约定一致> && uvicorn app.main:app --host 0.0.0.0 --port 8000`  
   - 多实例：额外设置 `AGENTS_REGISTRY_PATH=config/agents.yaml`（路径相对 `services/gateway`；YAML 结构见 [`services/gateway/config/agents.example.yaml`](../services/gateway/config/agents.example.yaml)），`routing` 须包含 `summary_from_chat`、`deliver_whiteboard`、`deliver_slides` 三个键。
2. 终端 B：前端  
   `cd apps/web && npm i && npm run dev`

## 流程

1. 浏览器打开 `http://127.0.0.1:5173`（经 Vite 代理打 Gateway，Cookie 同站）。
2. 页面自动 `POST /api/v1/auth/dev` 后拉取 **云空间目录** 列表；点 **「生成一条开发用总结」** 调 `POST /api/v1/dev/trigger-summary`，由外部 Agents 返回结果；在 `DEV_SKIP_LARK=true` 时可能为占位直链。若已配置真云盘与 `ARTIFACTS_DRIVE_FOLDER_TOKEN`，**刷新列表**可在目标目录看到新文档（延迟取决于飞书与 Agents）。
3. 勾选目标文档（以 `file_token` 为键），选 **仅画板** / **仅 PPT** / **两者**，点 **开始生成**；**不**轮询任务。按页面提示，隔一段时间在飞书该目录中 **「刷新列表」** 或直接在飞书查看新产出的画板/幻灯片。
4. 可选：用 curl 模拟飞书 `challenge`：  
   `curl -X POST http://127.0.0.1:8000/lark/events -H 'Content-Type: application/json' -d '{"challenge":"test123"}' `  
   应返回 `{"challenge":"test123"}`.

## 真机飞书

将开放平台中的事件 URL、网页应用、OAuth 重定向改为公网 Gateway，关闭 `DEV_SKIP_LARK` 并配置 `ARTIFACTS_DRIVE_FOLDER_TOKEN`；为自建应用开通 **`drive:drive:readonly`（或等价）** 与 **`docx:document` / `docx:document:create`** 等列目录、创建文档所需 scope 并发版（Gateway 使用 `tenant_access_token` 调 OpenAPI，不再依赖本机 `lark-cli`）。业务数据不存 Gateway 本地库。
