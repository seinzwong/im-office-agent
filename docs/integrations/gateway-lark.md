# Gateway 与 飞书开放平台 对接

## 自建应用

1. 在[飞书开放平台](https://open.feishu.cn/)（Lark 则为对应国际站）创建企业自建应用。

## 能力开关

- **机器人**（或「消息与群组」中接收消息）
- **网页** / 应用内网页：填写 `PUBLIC_WEB_BASE_URL` 指向的 H5 根地址
- **事件订阅**：
  - `im.message.receive_v1`（以控制台为准）
  - 请求地址：`{GATEWAY_PUBLIC_BASE_URL}/lark/events`
- **权限 scope**：`im:chat:readonly`、`im:message:readonly` 或读群消息历史所需 scope；回复消息所需 IM 发消息 scope；**云盘列目录**（`drive:drive:readonly` 等）、**创建云文档**（`docx:document` / `docx:document:create` 等）按 Gateway 当前 OpenAPI 调用申请。
- **文档创建身份**：如果目标文件夹属于用户个人云盘且未授权给应用，请配置 `FEISHU_USER_ACCESS_TOKEN` / `feishu_user_access_token`；留空时 Gateway 使用应用 `tenant_access_token` 创建文档。
- **用户授权**：`/summary` 会优先使用触发用户的 OAuth `user_access_token` 创建文档。用户未授权时，机器人会回复 `/api/v1/auth/login?state=...` 授权链接；授权完成后用户重新发送 `/summary`。

## 加解密

若启用 Encrypt Key：事件体为加密 JSON。Gateway 实现 **Encrypt Key 解密**（MVP 可在未配置时仅支持明文/测试用 challenge）。

## URL 验证

`POST /lark/events` 首包可能为 `{"type":"url_verification","challenge":"..."} `，应 **原样** 返回 `{"challenge":"..."} `。

## OAuth 重定向

在应用「安全设置」中配置**重定向 URL** 为 `OAUTH_REDIRECT_URI`（与 `gateway-bff` 中 `/api/v1/auth/callback` 一致）。

## JSSDK

- 将应用 **AppID** 与「网页」安全域名指向前端公网与 Gateway 可签名域名。  
- 调用 Gateway `POST /api/v1/jssdk/config` 获取 `signature`、`timestamp` 等供前端 `config`。

## 机器人可见回复

- 在收到 `@` 后，飞书将消息事件 POST 到 Gateway。
- 当前支持命令：
  - 只 `@` 机器人且无正文：回复开场白并 @ 触发用户。
  - `/help`：展示可用命令。
  - `/summary [minutes|all] [limit]`：拉取最近一段群聊并生成飞书文档；默认 `60` 分钟、最多 `200` 条，`limit` 上限为 `200`；传 `all` 可关闭时间窗限制。
  - 其他 `/` 指令：回复“该功能还没开发好，请检查已有指令”。
- `/summary` 流程：拉取群消息历史 -> 调用 Agent 生成 IR -> 用 IR 渲染并创建 docx -> 回复原消息，回复中包含统计时间窗、消息条数和文档链接。
- OAuth token 默认保存在 `.artifacts/auth/user_tokens.json`；可通过 `oauth_token_store_path` 覆盖。默认用户 scope 包含 `offline_access`、`docx:document:create`、`docx:document:write_only`、`drive:file:upload`、`space:document:retrieve` 和 `contact:user.base:readonly`。

> **注意**：Gateway 使用环境变量中的 **`LARK_APP_ID` / `LARK_APP_SECRET`** 换取 `tenant_access_token`；与飞书网页 OAuth 使用同一应用时，控制台 **scope 并集** 需覆盖上述能力。
