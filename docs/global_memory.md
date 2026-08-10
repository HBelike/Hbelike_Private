# 全局交接记忆

> 维护规则：当对话上下文预计达到约 70% 时，先更新本文件，再压缩上下文或交接。内容最多 12 条；不得记录 API Key、密码、服务器私钥或用户附件原文。

1. **目标**：将周榜公众号工作流、审核台、技能库、求职助手、面经库、登录/管理台完整上线到香港服务器，不削弱既有能力。
2. **代码链路**：本地与 GitHub `main`、服务器 `/home/ubuntu/apps/Hbelike_Private` 已同步到 `67b5b70`；本机通过独立 GitHub SSH key 推送，服务器通过只读 Deploy Key 拉取。
3. **服务器**：腾讯云香港 Ubuntu，公网 IP `43.155.86.239`，Docker 29.7.2 / Compose 5.4.0，资源约 4 vCPU / 7.4 GiB RAM / 89 GiB 可用磁盘；主机 UFW 未启用。
4. **生产拓扑**：Docker Compose 使用 Caddy（唯一 80/443 HTTPS 入口）、Vue/Nginx、FastAPI、PostgreSQL + pgvector、Gotenberg、单实例 `pipeline-scheduler`；旧公众号工作流继续使用共享卷中的 SQLite。
5. **生产配置**：服务器 `.env.production` 已创建且权限 `600`，含临时域名 `43.155.86.239.nip.io`、数据库随机口令和独立 Fernet 主密钥；外部 GitHub、微信、模型、邮件凭据保持空白，绝不从历史聊天或 Git 复用。
6. **鉴权与管理员**：生产开启 API 登录强制、闭合运营和 CLI 首管理员模式；首管理员只能通过 `scripts/bootstrap_first_admin.py` 的服务器交互式命令创建。
7. **模型凭据**：页面保存的 API Key 用 `CAREER_CREDENTIAL_MASTER_KEY` Fernet 加密后存 PostgreSQL；禁止记录或回显 Key，旧明文必须显式迁移。
8. **技能库**：`find-skills`、`ai-image-generation`、`og-image-design`、`grill-me` 已作为 `deploy/skill-seeds` 种子；持久卷中的已编辑 Skill 不会被重新部署覆盖。
9. **调度边界**：周五 08:00 生产、09:00 建微信草稿；Scheduler 与人工流水线共享文件锁，生产只能单实例运行。视频付费提交仍默认关闭。
10. **当前故障**：首次 Compose 构建已成功，迁移和 Skill seed 成功；`career-web` 因 Nginx 缺少静态 `root` 发生 `/index.html` 内部重定向循环，Caddy 未启动。修复已在 `docker/nginx/default.conf` 加入 `root /usr/share/nginx/html` 和 `index index.html`，待提交、推送、服务器拉取并重建 Web/Caddy。
11. **已验证**：前端构建、Python 编译/pip check、凭据加密/模型网关/API/登录访问控制/管理员 bootstrap/调度锁/Skill 便携性测试、Compose 静态校验均已通过；本次服务器日志确认根因。
12. **下一步**：停止故障残留容器但保留卷，提交 Nginx 修复并通过 GitHub 同步服务器，重建并验证 HTTPS/健康检查；之后创建首管理员、补充轮换后的外部凭据、将服务器 IP 加入微信白名单，最后域名解析为 `xingxingtech.cn`。
