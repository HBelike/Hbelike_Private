# 全局交接记忆

> 维护规则：当对话上下文预计达到约 70% 时，先更新本文件，再压缩上下文或交接。内容最多 12 条；不得记录 API Key、密码、服务器私钥或用户附件原文。

1. **目标**：将 GitHub 周榜公众号工作流、Web 审核台、技能库、求职助手、面经库、登录/管理台完整部署到香港服务器，不削弱既有能力。
2. **仓库**：`D:\MyPro\WechaOffiicialAccount`，分支 `main`，远端为私有仓库 `HBelike/Hbelike_Private`；当前生产化改动尚未提交，服务器尚未连接。
3. **生产拓扑**：Docker Compose 使用 Caddy（唯一 80/443 HTTPS 入口）、Vue/Nginx、FastAPI、PostgreSQL + pgvector、Gotenberg、单实例 `pipeline-scheduler`；旧公众号工作流仍使用共享持久卷中的 SQLite `data/app.db`。
4. **调度**：周五 08:00 生产内容、09:00 建草稿；Scheduler 与人工手动流水线使用共享文件锁互斥，不能横向扩容该 Scheduler。
5. **鉴权**：生产开启 `PLATFORM_AUTH_REQUIRED=true`、`PLATFORM_CLOSED_OPERATOR_MODE=true`；除健康检查和 `/api/auth/*` 外 API 强制 Cookie 登录，首发仅管理员可操作业务。
6. **首管理员**：生产开启 `PLATFORM_CLI_BOOTSTRAP_ONLY=true` 和关闭公开注册；首次管理员只能用服务器 TTY 运行 `scripts/bootstrap_first_admin.py` 创建，避免公网抢注。
7. **模型凭据**：页面保存的 API Key 使用 `CAREER_CREDENTIAL_MASTER_KEY` 的 Fernet 加密后存 PostgreSQL；历史本地明文待配置有效主密钥后通过 `scripts/migrate_career_legacy_credentials.py` 迁移。不得记录或回显任何 Key。
8. **技能库**：已将 `find-skills`、`ai-image-generation`、`og-image-design`、`grill-me` 作为 `deploy/skill-seeds/` 种子；`skill-seed` 初始化持久卷且默认不覆盖 WebUI 已保存版本。
9. **生产环境**：真实 Secret 仅放服务器 `.env.production`；模板包含数据库、邮件、GitHub、DeepSeek、微信、媒体、模型、向量化、Fernet 等变量名。服务器固定公网 IP 仍需加入微信白名单。
10. **验证已通过**：前端 `npm run build`、Python compile/pip check、凭据加密/模型网关/API/登录访问控制/管理员 bootstrap/调度锁/Skill 便携性测试、Docker Compose 静态校验均已通过；仅有无阻塞的 Starlette/httpx 弃用警告。
11. **未解决外部条件**：需要服务器 SSH 登录方式、域名及 DNS 是否已解析、服务器能否访问 Docker Hub；私有仓库拉取需在服务器配置只读 Deploy Key 或 Fine-grained Token。
12. **下一步**：提交生产化改动到私有仓库；收到服务器信息后创建 `.env.production`、拉取镜像/启动、创建首管理员、HTTPS 与登录验收，最后验证 Scheduler、微信草稿和备份恢复。
