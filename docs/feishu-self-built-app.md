# 飞书侧自建应用与机器人（配置清单）

本项**不在本仓库内部署**；以下为与 Gateway 联调时需在[开放平台](https://open.feishu.cn/)完成的配置。Lark 国际站步骤类似。

## 1. 创建企业自建应用

- 应用名称、图标按团队要求填写。

## 2. 凭证与安全

- 记录 **App ID、App Secret**，写入部署环境或「凭证托管」，由 Gateway 使用；**不要** 提交到 Git。  
- **Encrypt Key、Verification Token** 用于事件；若未启用加解密，可在开发阶段在控制台**关闭**加密以简化 `POST /lark/events` 的 MVP。

## 3. 能力开关

- **机器人**：在「消息与群组 / 消息」中启用接收 `im` 相关事件；可见范围与可用范围与测试群一致。  
- **网页** / 应用内网页：主页 URL 填为前端线上地址（`PUBLIC_WEB_BASE_URL`），与 **安全域名、重定向 URL** 一致。  
- **事件订阅**  
  - 类型：`im.message.receive_v1`（以控制台实际为准）  
  - 请求地址：`{GATEWAY_PUBLIC_BASE_URL}/lark/events` 须 **HTTPS 公网** 可达。

## 4. 权限 (scope)

按实际功能申请：`im:chat:readonly` / `im:message:readonly`、发消息、**云盘列目录**（如 `drive:drive:readonly` 或 `space:document:retrieve`）、**创建 docx**（如 `docx:document` / `docx:document:create`）等；交付画板/幻灯片若后续改走 OpenAPI，再按需追加对应 scope。Gateway 默认用 **`tenant_access_token`** 调 OpenAPI，在控制台核对 **scope 并集** 已开通并发版。

## 5. 机器人可见回复

用户 @ 机器人后，飞书将消息事件 POST 到 Gateway。Gateway 完成 `summary_from_chat` 后，可调用[发送消息](https://open.feishu.cn/document)等接口将**文档链接**发到群内（在 Gateway 内用 **`tenant_access_token`** 调 OpenAPI 即可）。

## 6. 与本文档的交叉引用

- 详细 HTTP 与 OAuth：[integrations/gateway-lark.md](integrations/gateway-lark.md)  
- 前端 BFF：[integrations/gateway-bff.md](integrations/gateway-bff.md)  
- Mock 联调：[demo-script.md](demo-script.md)
