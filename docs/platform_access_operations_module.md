# 平台访问、运行配置与观测模块

## 设计目标

平台从本地单人预览演进为可部署的个人产品时，需要把“谁可以使用”“谁可以改生产参数”“一次任务按什么参数运行”从前端状态和 YAML 文件中拆开。本模块提供最小但可扩展的账号、角色、会话和配置版本能力；不修改既有微信公众号工作流、技能库或求职助手的业务数据模型。

## 方案与取舍

- **身份与会话**：PostgreSQL 保存账号、邮箱身份、角色和会话摘要，浏览器只保存 HttpOnly Cookie。密码使用标准库 `scrypt` 加盐散列；验证码使用服务端 HMAC 摘要，不保存明文密码、验证码或 API Key。
- **注册与角色**：首个管理员通过邮箱验证码 bootstrap；其余用户可公开注册并默认获得 `viewer`。`viewer` 只能查看，`operator` 可执行人工导入和手动任务，`admin` 可管理账号、运行配置与 LangSmith 观测。存量管理员可先按旧用户名登录，再绑定邮箱。
- **会话策略**：每次有效请求将空闲会话续至最多 7 天，但任何会话从登录时起最长 30 天；密码重置会撤销该账号的全部旧会话。
- **邮件投递**：验证码使用 Resend HTTP API；服务端只读取 `RESEND_API_KEY`、`RESEND_FROM_ADDRESS` 与 `PLATFORM_EMAIL_CODE_SECRET`。验证码有效 10 分钟、60 秒发送冷却、最多 5 次输入尝试。
- **运行配置**：每一次保存会形成 JSONB 版本快照。手动/定时流水线读取已激活版本并将版本号写入执行记录，避免编辑中的参数影响运行中的任务。
- **观测**：LangSmith 是平台一级路由。LangChain 调用使用 `run_name`，非 LangChain 调用使用轻量 trace/span；默认只记录脱敏元数据、状态和用量，不上传简历、图片、Cookie、密码或 API Key。页面优先 iframe 嵌入，遇到 CSP、X-Frame-Options 或第三方 Cookie 限制时提供新窗口打开。

## 调用链

```text
LoginPage
  -> /api/auth/bootstrap-status
  -> /api/auth/bootstrap/send-code | /api/auth/register/send-code
  -> /api/auth/*/verify | /api/auth/login
  -> HttpOnly platform_session Cookie
  -> /api/auth/me
  -> 前端路由守卫
      -> 管理员配置 / 可观测性 / 人工运行入口
      -> 后端角色依赖校验
```

## 数据边界与部署

- 新表位于 `career_assistant` schema：`platform_users`、`platform_sessions`、`platform_email_challenges`、`pipeline_config_versions`、`pipeline_execution_requests`。
- migration 只新增表和索引，不修改现有会话、面经、技能库或微信公众号表。
- 生产环境必须通过 `CAREER_DATABASE_URL` 连接 PostgreSQL，并以 HTTPS 部署 Cookie；本地开发可通过 HTTP 使用同一代码路径。
- LangSmith Key、模型 Key、平台登录 Cookie 只能放在服务端环境变量或受控密钥服务，永不进入页面接口。

## 验证清单

1. 迁移后可查询 bootstrap 状态。
2. 首个管理员和普通用户均需完成邮箱验证码；第二次 bootstrap 被拒绝。
3. 未登录访问管理员接口返回 401，非 admin 返回 403。
4. 登录后刷新页面仍可通过会话 Cookie 恢复身份；退出后 Cookie 和服务端会话一并失效。
5. 配置保存生成递增版本，执行记录关联固定版本。
