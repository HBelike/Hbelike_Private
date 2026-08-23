# 全局交接记忆

> 维护规则：当对话上下文预计达到约 70% 时，先更新本文件，再压缩上下文或交接。内容最多 12 条；不得记录 API Key、密码、服务器私钥或用户附件原文。

1. **目标**：将周榜公众号工作流、审核台、技能库、求职助手、面经库、登录/管理台完整上线到香港服务器，不削弱既有能力。
2. **代码链路**：本地、GitHub `main` 与服务器 `/home/ubuntu/apps/Hbelike_Private` 使用同一发布链路；本机通过独立 GitHub SSH key 推送，服务器通过只读 Deploy Key 拉取。
3. **服务器**：腾讯云香港 Ubuntu，公网 IP `43.155.86.239`，Docker 29.7.2 / Compose 5.4.0，资源约 4 vCPU / 7.4 GiB RAM / 89 GiB 可用磁盘；主机 UFW 未启用。
4. **生产拓扑**：Docker Compose 使用 Caddy（唯一 80/443 HTTPS 入口）、Vue/Nginx、FastAPI、PostgreSQL + pgvector、Gotenberg、单实例 `pipeline-scheduler`；旧公众号工作流继续使用共享卷中的 SQLite。
5. **生产配置**：服务器 `.env.production` 权限为 `600`，已切换 `APP_DOMAIN=xingxingtech.cn`，含数据库随机口令和独立 Fernet 主密钥；周榜所需 GitHub、DeepSeek、火山方舟和微信公众号凭据已写入生产环境且未回显，邮件与可选 Provider 仍按需配置。凡曾在聊天或旧文件中出现过的凭据均应在首次验收后轮换。
6. **鉴权与管理员**：生产开启 API 登录强制、CLI 首管理员模式和公开邮箱注册；线上已存在首管理员，不应重复执行 bootstrap。登录页支持邮箱验证码、邮箱密码、注册和找回密码，注册用户默认获得基础访问权限。
7. **模型凭据**：页面保存的 API Key 用 `CAREER_CREDENTIAL_MASTER_KEY` Fernet 加密后存 PostgreSQL；禁止记录或回显 Key，旧明文必须显式迁移。
8. **技能库**：`find-skills`、`ai-image-generation`、`og-image-design`、`grill-me`、`github-project-blog` 已作为 `deploy/skill-seeds` 种子；持久卷中的已编辑 Skill 不会被重新部署覆盖。
9. **调度边界**：周五 08:00 生产、09:00 建微信草稿；Scheduler 与人工流水线共享文件锁，生产只能单实例运行。本地默认保持音视频关闭；生产已显式设置 `VIDEO_SUBMIT_ENABLED=true`、`AUDIO_ENABLED=true`，会运行 Seedance、视觉质检、豆包 TTS 与最终视频装配并产生相应调用。
10. **上线修复**：`eba2f08` 为生产 Nginx 增加静态 `root /usr/share/nginx/html` 与 `index index.html`；服务器已通过标准 Git 拉取、重建 Web/Caddy，`career-web` 健康检查恢复正常。
11. **当前生产版本**：2026-08-23 已部署 Chrome 浏览器“面试大师”提交 `94190a3`，PostgreSQL 迁移至 `20260823_19`；公网页面与健康接口返回 200，API/Web/PostgreSQL/Scheduler 均正常，Caddy 允许同源麦克风并继续禁止摄像头。浏览器通过标签页音频、同源 WSS、DashScope Qwen-Audio Streaming 和现有文本模型完成实时转写与中文回答；Electron 旧方案保留不变。
12. **当前边界**：登录页支持邮箱验证码、邮箱密码、显式注册和找回密码；生产保持 `PLATFORM_AUTH_REQUIRED=true`、`PLATFORM_CLOSED_OPERATOR_MODE=true`、`PLATFORM_PUBLIC_REGISTRATION_ENABLED=true`、`PLATFORM_CLI_BOOTSTRAP_ONLY=true` 与 `CAREER_REDACTION_ENABLED=true`。求职助手模型选择器只展示真实可用模型；前端仅维护电脑端与手机端。
