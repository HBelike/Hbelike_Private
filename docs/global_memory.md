# 全局交接记忆

> 维护规则：当对话上下文预计达到约 70% 时，先更新本文件，再压缩上下文或交接。内容最多 12 条；不得记录 API Key、密码、服务器私钥或用户附件原文。

1. **目标**：将周榜公众号工作流、审核台、技能库、求职助手、面经库、登录/管理台完整上线到香港服务器，不削弱既有能力。
2. **代码链路**：本地、GitHub `main` 与服务器 `/home/ubuntu/apps/Hbelike_Private` 已同步到 `8fb4d96`；本机通过独立 GitHub SSH key 推送，服务器通过只读 Deploy Key 拉取。
3. **服务器**：腾讯云香港 Ubuntu，公网 IP `43.155.86.239`，Docker 29.7.2 / Compose 5.4.0，资源约 4 vCPU / 7.4 GiB RAM / 89 GiB 可用磁盘；主机 UFW 未启用。
4. **生产拓扑**：Docker Compose 使用 Caddy（唯一 80/443 HTTPS 入口）、Vue/Nginx、FastAPI、PostgreSQL + pgvector、Gotenberg、单实例 `pipeline-scheduler`；旧公众号工作流继续使用共享卷中的 SQLite。
5. **生产配置**：服务器 `.env.production` 权限为 `600`，已切换 `APP_DOMAIN=xingxingtech.cn`，含数据库随机口令和独立 Fernet 主密钥；周榜所需 GitHub、DeepSeek、火山方舟和微信公众号凭据已写入生产环境且未回显，邮件与可选 Provider 仍按需配置。凡曾在聊天或旧文件中出现过的凭据均应在首次验收后轮换。
6. **鉴权与管理员**：生产开启 API 登录强制、闭合运营和 CLI 首管理员模式；线上已存在首管理员，公开注册关闭，不应重复执行 bootstrap。
7. **模型凭据**：页面保存的 API Key 用 `CAREER_CREDENTIAL_MASTER_KEY` Fernet 加密后存 PostgreSQL；禁止记录或回显 Key，旧明文必须显式迁移。
8. **技能库**：`find-skills`、`ai-image-generation`、`og-image-design`、`grill-me` 已作为 `deploy/skill-seeds` 种子；持久卷中的已编辑 Skill 不会被重新部署覆盖。
9. **调度边界**：周五 08:00 生产、09:00 建微信草稿；Scheduler 与人工流水线共享文件锁，生产只能单实例运行。视频付费提交仍默认关闭；审核通过前不进入 `ArticleLayoutTask` / `DeliverTask`。
10. **上线修复**：`eba2f08` 为生产 Nginx 增加静态 `root /usr/share/nginx/html` 与 `index index.html`；服务器已通过标准 Git 拉取、重建 Web/Caddy，`career-web` 健康检查恢复正常。
11. **已验证**：前端构建、Python 编译/pip check、凭据加密/模型网关/API/登录访问控制/管理员 bootstrap/调度锁/Skill 便携性测试、Compose 静态校验均已通过；公网 `https://xingxingtech.cn/api/health` 返回 200，Caddy 已取得 Let’s Encrypt 证书。真实周榜测试已成功完成 Search、Summary、短视频蓝图、Seedream 五图、Edge TTS、存储和预览；GitHub API 与微信公众号 access token 均已连通。
12. **当前修复/下一步**：面经库仍按“公开正文/图片 OCR → `InterviewEvidenceAnalyzer` 清洗 → 用户确认 → 手动入库”执行。2026-08-12 手动完整流水线已修复：请求状态在启动线程前同步写入 `running`，并修正 PostgreSQL `metadata_json` 更新的参数类型歧义；过期 `queued` 会显式标记失败。最新真实运行已完成启动和 Search，但 Summary 因千分位数字格式误判失败；现已兼容精确的千分位展示、仍拒绝近似数。下一次运行需从工作台重新提交并观察终态。
