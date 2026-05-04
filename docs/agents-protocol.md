# Agents 统一交互协议

实现方：外部 Agents 服务；本仓库 Gateway 仅作为 **HTTP 客户端** 按约定调用。

**协议全文（端点、信封、各 action 的 payload/result、错误约定）以 [services/gateway/README.md](../services/gateway/README.md) 中「Agents 调用协议」章节为准。**

## 路由与配置（摘要）

- **端点**：`POST {base_url}/v1/invoke`；`base_url` 来自 **`gateway.yaml`** 的 `agents_base_url`，或由注册表按 `action` 解析。
- **鉴权**：`Authorization: Bearer <M2M_TOKEN>`（与 [integrations/gateway-agents.md](integrations/gateway-agents.md) 中的 `agents_m2m_token` / 各实例 token 一致）。

## Gateway 多实例路由

部署多个 Agents 时，各实例仍实现同一 `POST /v1/invoke`。Gateway 通过 `gateway.yaml` 的 `agents_registry_path` 指向的 YAML，将 `summary_from_chat`、`deliver_whiteboard`、`deliver_slides` 路由到不同 `base_url`。示例见 [`services/gateway/app/agents/agents.example.yaml`](../services/gateway/app/agents/agents.example.yaml)。

## 错误码映射（摘要）

`ok: false` 时返回 `error: { code, message, details? }`；与前端/Bot 文案映射见 [gateway-agents.md](integrations/gateway-agents.md)。
