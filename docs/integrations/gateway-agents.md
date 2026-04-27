# Gateway 与 外部 Agents 对接

- **只经 HTTP**：Gateway 为唯一调用方，Agents 不连前端、不直持 `lark-cli`。
- **基址**：`AGENTS_BASE_URL`
- **鉴权**：`Authorization: Bearer ${AGENTS_M2M_TOKEN}`
- **协议**：[../agents-protocol.md](../agents-protocol.md) 与 [../../specs/agents-protocol/openapi.yaml](../../specs/agents-protocol/openapi.yaml)
- **超时**：`summary_from_chat` 建议 30–60s，交付 60–120s（按部署配置）。

## 错误码映射（建议）

| Agents `error.code` | 前端/Bot 用户文案 |
|---------------------|-------------------|
| `INVALID_TIME_WINDOW` | 无法解析时间范围，已使用默认过去 24 小时。 |
| `UPSTREAM_LLM`        | 生成服务繁忙，请重试。 |
| `PAYLOAD_INVALID`     | 参数错误。 |

> 在 Gateway 内维护一张表，避免把内部堆栈直接暴露到 IM。

## Mock 联调

`mocks/agents` 实现同一路径 `POST /v1/invoke`，与生产 Agents 可互换，仅变 `AGENTS_BASE_URL`。
