# 全平台生产部署基线

## 设计目标

本文件定义当前个人平台的首个公网部署形态：在一台香港 Linux VPS 上完整运行 GitHub 周榜公众号工作流、审核台、技能库、求职助手、面经库、登录/管理台与 PostgreSQL + pgvector，同时不把数据库、FastAPI 或临时附件直接暴露到公网。

首发采用“闭合运营模式”：只有管理员登录后可以调用业务 API。这样在 Career 细粒度租户授权与旧审核模块的角色授权尚未完全拆分前，仍可安全保留所有既有功能。

## 架构与调用链

```text
浏览器
  -> Caddy :443（自动 HTTPS、静态 Web、API 反向代理）
  -> career-web（Vue 静态页面）
  -> career-api（FastAPI：审核、Skill、Career、面经、登录、管理台）
      -> PostgreSQL + pgvector（会话、账号、面经、模型档案、运行配置）
      -> SQLite 共享卷（既有公众号任务、审核、素材索引）
      -> outputs 共享卷（图片、音频、视频）

pipeline-scheduler（唯一副本）
  -> main.py
  -> Application.run()
  -> SchedulerManager
  -> 周五 08:00 内容生产 / 09:00 微信草稿推进
```

## 技术取舍

- **Caddy**：使用 Caddy 2 自动签发/续期 HTTPS，避免宿主机 Nginx + Certbot 的额外维护；它只作为公网入口。Caddy 为 Apache-2.0 许可，适合该单机部署。
- **单一 Scheduler**：既有周榜工作流仍使用 SQLite，因此只能运行一个 `pipeline-scheduler` 副本，并与 API 共用 `application_data` 卷。未来迁移 PostgreSQL 或引入分布式锁后再扩容。
- **执行互斥**：Scheduler 与管理台手动运行共享同一个原子文件锁；发生冲突时 Scheduler 记录跳过，手动执行得到明确失败记录。锁的保守过期时间为六小时，防止容器崩溃留下永久阻塞。
- **双数据库过渡**：Career/账号/RAG 使用 PostgreSQL + pgvector；原公众号工作流继续使用 SQLite。此次上线不改变旧业务数据模型，优先确保稳定发布。
- **媒体与附件**：周榜产物使用 Docker 命名卷持久化；原始简历附件使用 API tmpfs，在 Turn 完成后删除。生产环境必须保持 `CAREER_REDACTION_ENABLED=true`。
- **Skill 持久化**：`deploy/skill-seeds` 是随镜像发布、经过审查的 `SKILL.md` 快照；一次性 `skill-seed` 容器会先将缺失种子复制到独立 `application_skills` 卷并授予 API 进程写入权限。之后 WebUI 的保存结果优先且永不被重新部署覆盖，容器重建后仍可继续查看、编辑与每周刷新 Star。生产服务器不依赖开发电脑的 `~/.agents`、`~/.codex` 或插件缓存。
- **认证边界**：生产设 `PLATFORM_AUTH_REQUIRED=true` 与 `PLATFORM_CLOSED_OPERATOR_MODE=true`。登录通过 HttpOnly Cookie，认证中间件会把真实平台用户映射为 Career Actor，避免所有登录用户共用默认对话身份。

## 部署前置条件

1. Ubuntu 22.04/24.04 或等价 Linux，建议至少 2 vCPU / 4 GB RAM / 60 GB SSD；需要完整视频生产时建议 4 vCPU / 8 GB。
2. 安装 Docker Engine 与 Docker Compose v2；不需要在服务器安装 Python、Node.js 或 PostgreSQL。
3. 一个已解析到服务器固定公网 IPv4 的域名；安全组和 UFW 仅放行 22（限管理 IP）、80、443。
4. 私有仓库使用只读 Deploy Key 拉取；服务器不使用个人 GitHub Token。真实 `.env.production` 在服务器本地创建，权限为 `600`。
5. `application_data` Docker 命名卷必须保留：未显式设置 `CAREER_CREDENTIAL_MASTER_KEY` 时，API 会在此卷自动创建并复用模型凭据主密钥。
6. 微信公众平台的 API IP 白名单加入该服务器的固定出口 IP；当前项目仅主动调用微信接口，不需要配置公众号入站消息回调。

## 首次部署

```bash
git clone git@github.com:HBelike/Hbelike_Private.git /opt/wechat-agent-platform
cd /opt/wechat-agent-platform
cp .env.production.example .env.production
chmod 600 .env.production
# 编辑 .env.production，填写域名、数据库密码、账号邮件服务、微信与模型凭据。
# 首次部署先保持 VIDEO_SUBMIT_ENABLED=false、AUDIO_ENABLED=false；媒体前置检查通过后再显式开启。
# CAREER_CREDENTIAL_MASTER_KEY 可留空，API 首次启动会在 application_data 卷自动创建。
docker compose --env-file .env.production -f docker-compose.production.yml config --quiet
docker compose --env-file .env.production -f docker-compose.production.yml up -d --build
docker compose --env-file .env.production -f docker-compose.production.yml ps
# 仅升级自旧版本时执行：将历史 plaintext_api_key 转为 Fernet 密文。
docker compose --env-file .env.production -f docker-compose.production.yml run --rm career-api python scripts/migrate_career_legacy_credentials.py
```

首次启动后，Caddy 会在 DNS 已生效且 80/443 可达时自动获取证书。迁移服务成功退出后，API、Web 与 Scheduler 才会运行。

`skill-seed` 也必须以退出码 0 完成：它只将 `deploy/skill-seeds` 中目标卷尚不存在的
`SKILL.md` 增量写入 `application_skills`，因此可安全地随每次镜像重建运行。不要将开发机
的整套 `.codex`/插件目录拷贝到服务器；如需增加项目 Skill，应先在开发机审查后通过
`scripts/export_portable_skills.py` 导出到 `deploy/skill-seeds` 并提交。详细规则见
[`deploy/skill-seeds/README.md`](../deploy/skill-seeds/README.md)。

模型凭据迁移脚本只输出数量；看到 `pending_after=0` 后，确认 `.env.production` 中 `CAREER_ALLOW_LEGACY_PLAINTEXT_CREDENTIALS=false`（或不设置）。若输出 `legacy_unknown` 大于 0，代表早期没有可验证格式的旧密文，不能强行转换，必须在模型设置页重新填写这些 API Key。

## 验证与验收

```bash
curl -fsS https://<你的域名>/api/health
docker compose --env-file .env.production -f docker-compose.production.yml logs --tail=120 career-migrate
docker compose --env-file .env.production -f docker-compose.production.yml logs --tail=120 skill-seed
docker compose --env-file .env.production -f docker-compose.production.yml logs --tail=120 pipeline-scheduler
docker compose --env-file .env.production -f docker-compose.production.yml ps
docker compose --env-file .env.production -f docker-compose.production.yml exec career-api printenv VIDEO_SUBMIT_ENABLED AUDIO_ENABLED
docker compose --env-file .env.production -f docker-compose.production.yml exec pipeline-scheduler printenv VIDEO_SUBMIT_ENABLED AUDIO_ENABLED
docker compose --env-file .env.production -f docker-compose.production.yml exec career-api python scripts/check_media_production_readiness.py
```

验收顺序：`skill-seed` 日志显示已导入或保留种子 → HTTPS 页面可访问 → 邮箱 bootstrap 创建首个管理员 → 登录后打开技能库并确认 `find-skills` 等项目本地 Skill 可见且可编辑 → 访问工作台与 Career → 验证 PostgreSQL 会话持久化 → 查看 Scheduler 日志中的下次周五执行时间 → 运行不发起外部请求的媒体前置检查 → 创建微信草稿 → 确认首次备份可恢复。

## 备份与后续边界

- 每日对 PostgreSQL 执行 `pg_dump -Fc`，并备份 `application_data`（SQLite）、`application_outputs`（媒体）与 `application_skills`（编辑后的 Skill）到异地对象存储；保留至少 7 个日备份与 4 个周备份。
- Caddy 的 `caddy_data`、`caddy_config` 卷必须保留，否则会丢失证书账户状态。
- 首发不启用 GPU Docling；有 NVIDIA GPU 的服务器再使用 `document-processing` profile。
- 仓库和本地默认配置保持 `video.submit_enabled=false`、`audio.enabled=false`，避免开发调试产生费用。当前生产环境显式设置 `VIDEO_SUBMIT_ENABLED=true`、`AUDIO_ENABLED=true`，运行完整音视频链路；每次调整后必须重建 API 与 Scheduler 并执行上面的容器内检查。
- 旧公众号 SQLite 工作流、Skill 文件持久化、多用户 Career 角色授权、Docker secrets 与 CI/CD 是下一阶段演进项，不应在未验证的情况下横向扩容。
