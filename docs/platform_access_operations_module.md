# 平台访问、运行配置与观测模块

## 设计目标

平台从本地单人预览演进为可部署的个人产品时，需要把“谁可以使用”“谁可以改生产参数”“一次任务按什么参数运行”从前端状态和 YAML 文件中拆开。本模块提供最小但可扩展的账号、角色、会话和配置版本能力；不修改既有微信公众号工作流、技能库或求职助手的业务数据模型。

## 方案与取舍

- **身份与会话**：PostgreSQL 保存账号、邮箱身份、角色和会话摘要，浏览器只保存 HttpOnly Cookie。密码使用标准库 `scrypt` 加盐散列；验证码使用服务端 HMAC 摘要，不保存明文密码、验证码或 API Key。
- **注册与角色**：首个管理员通过邮箱验证码 bootstrap；其余用户可公开注册并默认获得 `viewer`。`viewer` 只能查看，`operator` 可执行人工导入和手动任务，`admin` 可管理账号、运行配置与 LangSmith 观测。存量管理员可先按旧用户名登录，再绑定邮箱。
- **会话策略**：每次有效请求将空闲会话续至最多 7 天，但任何会话从登录时起最长 30 天；密码重置会撤销该账号的全部旧会话。
- **邮件投递**：验证码使用 Resend HTTP API；服务端只读取 `RESEND_API_KEY`、`RESEND_FROM_ADDRESS` 与 `PLATFORM_EMAIL_CODE_SECRET`。验证码有效 10 分钟、60 秒发送冷却、最多 5 次输入尝试。
- **发件域名**：`resend.dev` 仅可用于向 Resend 账户自身的登录邮箱做测试，不能向 QQ 等其他邮箱投递。正式运行需在 Resend 验证自己的域名，并把 `RESEND_FROM_ADDRESS` 配为该域名下的地址；失败时系统会删除未投递挑战，用户可在修复配置后立即重新发送。
- **注册开关**：生产环境的 `PLATFORM_PUBLIC_REGISTRATION_ENABLED=true` 时，用户先通过邮箱验证码完成注册，再以邮箱和密码登录；设为 `false` 时仅保留已有账号登录、绑定邮箱与密码找回。首个管理员仍由服务器 CLI 初始化，避免首账号被公网抢注。
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
6. 使用 `resend.dev` 向非账户邮箱投递时，接口返回明确的域名验证提示；发件失败后不会留下冷却中的验证码挑战。

## 认证门户界面调整（2026-08-13）

- **设计目标**：把登录、注册和密码找回收敛为单任务认证门户，减少双标签、状态徽标和长期说明造成的视觉噪音，同时保留职业工作台的品牌识别。
- **技术取舍**：桌面端使用品牌叙事与 420px 单列表单的双栏结构；注册入口、密码找回改为文字级次要动作。`900px` 以下隐藏装饰面板，仅保留紧凑品牌和认证表单，避免移动端横向溢出。
- **调用链**：`LoginPage.vue` 根据 `/login`、`/register`、`/forgot-password` 选择表单状态，仍调用原有 `/api/auth/*` 接口；本次没有修改认证协议、数据表或邮件投递逻辑。
- **验证结果**：本地桌面端三条认证路由完成视觉检查；`390×844` 登录页无横向溢出，前端控制台无 error，`npm run build` 与 `git diff --check` 通过。
- **发布边界**：当前调整只在本地工作区验证，未提交、未推送、未更新生产环境；需在用户明确授权后再发布。
