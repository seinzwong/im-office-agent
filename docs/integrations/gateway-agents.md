# Gateway 与 外部 Agents 对接

- **只经 HTTP**：Gateway 为唯一调用方，Agents 不连前端、不直持 `lark-cli`。
- **协议**：[../agents-protocol.md](../agents-protocol.md)；正文见 [../../services/gateway/README.md](../../services/gateway/README.md) 内「Agents 调用协议」
- **超时**：`summary_from_chat` 建议 30–60s，交付 60–120s（按部署配置）。

## 单实例（默认）

在 **`services/gateway/gateway.yaml`**（模板见 [`gateway.example.yaml`](../../services/gateway/gateway.example.yaml)）中配置：

- **基址**：`agents_base_url`（所有 `action` 共用）
- **鉴权**：`agents_m2m_token`（对应 `Authorization: Bearer …`）

## 多实例（注册表）

当在 **`gateway.yaml`** 中设置 **`agents_registry_path`** 时，Gateway 在启动时加载 YAML，按 **`routing` 中的 `action` → `agent_id`** 选择 `POST {base_url}/v1/invoke` 的目标；信封与单实例模式相同。

- 路径：可为绝对路径，或**相对 `services/gateway` 目录**的相对路径（例如 `app/agents/agents.yaml`）。
- 结构：见与代码同目录的示例 [`../../services/gateway/app/agents/agents.example.yaml`](../../services/gateway/app/agents/agents.example.yaml)。
- **`routing`**：必须包含协议中的三个 `action`：`summary_from_chat`、`deliver_whiteboard`、`deliver_slides`。
- **`agents`**：每个 `agent_id` 至少含 `base_url`；`m2m_token` 可选，缺省时使用 **`gateway.yaml`** 中的 `agents_m2m_token`。

未设置 `agents_registry_path` 时，行为与仅配置 `agents_base_url` 一致。

单实例与多实例的键名均见 [`gateway.example.yaml`](../../services/gateway/gateway.example.yaml)。

## 错误码映射（建议）

| Agents `error.code` | 前端/Bot 用户文案 |
|---------------------|-------------------|
| `INVALID_TIME_WINDOW` | 无法解析时间范围，已使用默认过去 24 小时。 |
| `UPSTREAM_LLM`        | 生成服务繁忙，请重试。 |
| `PAYLOAD_INVALID`     | 参数错误。 |

> 在 Gateway 内维护一张表，避免把内部堆栈直接暴露到 IM。

## 本地与联调

部署符合上述 OpenAPI 的外部 Agents 后，在 **`gateway.yaml`** 中完成单实例或注册表相关配置，再启动 Gateway 与前端即可联调。
