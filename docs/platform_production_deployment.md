# 全平台生产部署基线

## 设计目标

本文件定义当前个人平台的首个公网部署形态：在一台香港 Linux VPS 上完整运行 GitHub 周榜公众号工作流、审核台、技能库、求职助手、面经库、登录/管理台与 PostgreSQL + pgvector，同时不把数据库、FastAPI 或临时附件直接暴露到公网。

平台采用二元角色：固定邮箱管理员负责路由与运行配置，其余账号统一为普通用户。所有业务 API 必须先登录；普通用户按管理员保存的模块开关访问普通页面，管理员专属接口继续由 FastAPI 服务端角色依赖保护。

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
- **Skill 持久化**：`deploy/skill-seeds` 是随镜像发布、经过审查的 `SKILL.md` 快照；一次性 `skill-seed` 容器会先将缺失种子复制到独立 `application_skills` 卷并授予 API 进程写入权限。`deploy/skill-seeds/.skill-lock.json` 仅记录已核验的公开 GitHub 来源，用于恢复真实 Star 数据。API 与单一 Scheduler 共用该卷和 `application_data`，Scheduler 启动及每周内容任务会刷新已过期的 GitHub Star 快照。之后 WebUI 的保存结果优先且永不被重新部署覆盖；生产服务器不依赖开发电脑的 `~/.agents`、`~/.codex` 或插件缓存。
- **认证边界**：生产设 `PLATFORM_AUTH_REQUIRED=true`。登录通过 HttpOnly Cookie，认证中间件会把真实平台用户映射为 Career Actor，避免所有登录用户共用默认对话身份；`admin` 仅允许 `2963613812@qq.com` 持有，其余账号统一为 `user`。部署面经管理员权限版本后，健康接口的 `career_runtime_revision` 应为 `2026-08-28-interview-admin-permissions-v2`。

## 部署前置条件

1. Ubuntu 22.04/24.04 或等价 Linux，建议至少 2 vCPU / 4 GB RAM / 60 GB SSD；需要完整视频生产时建议 4 vCPU / 8 GB。
2. 安装 Docker Engine 与 Docker Compose v2；不需要在服务器安装 Python、Node.js 或 PostgreSQL。
3. 一个已解析到服务器固定公网 IPv4 的域名；安全组和 UFW 仅放行 22（限管理 IP）、80、443。
4. 私有仓库使用只读 Deploy Key 拉取；服务器不使用个人 GitHub Token。真实 `.env.production` 在服务器本地创建，权限为 `600`。
5. `application_data` Docker 命名卷必须保留：未显式设置 `CAREER_CREDENTIAL_MASTER_KEY` 时，API 会在此卷自动创建并复用模型凭据主密钥。
6. 微信公众平台的 API IP 白名单加入该服务器的固定出口 IP；当前项目仅主动调用微信接口，不需要配置公众号入站消息回调。
7. 需要启用面经库“全网公开信息收集”时，在服务器 `.env.production` 配置 `FIRECRAWL_API_KEY`；Hosted Firecrawl 使用默认 `FIRECRAWL_API_URL=https://api.firecrawl.dev`。

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
- 旧公众号 SQLite 工作流、Skill 文件持久化、更细粒度的用户资源授权、Docker secrets 与 CI/CD 是下一阶段演进项，不应在未验证的情况下横向扩容。

## 2026-08-26 全量生产部署记录

- **发布目标**：将求职助手招呼语学历/成果链接强化、多岗位逐岗触发发送、面经归属编辑、输入框 Skill/面经内联高亮、空指标过滤、路由标题盒与面经导入操作盒统一发布到 `xingxingtech.cn`。
- **发布版本**：业务代码提交 `5618965`，生产仓库从 `0afb857` 快进更新；本地 `data/`、临时 PDF、缓存、本机 `.agents/skills` 和 `src/test.py` 未进入提交或生产镜像。
- **发布前验证**：Python 全量 `244 passed`，WebUI 全量 `138 passed`，浏览器扩展全量 `21 passed`；Service Worker 语法检查、求职助手部署配置检查、Vite production build 和扩展 `0.2.7` 确定性打包通过。
- **部署调用链**：生产 `.env.production` 配置展开通过 → `git pull --ff-only origin main` → `docker compose --env-file .env.production -f docker-compose.production.yml up -d --build` → `skill-seed` 与 `career-migrate` 退出码均为 `0` → API、Worker、Scheduler、Web 重建完成。
- **数据库与运行状态**：Alembic 为 `20260825_21 (head)`；API、Web、PostgreSQL 均为 healthy，Worker、Scheduler、Caddy、Docling、Gotenberg 正常运行；`VIDEO_SUBMIT_ENABLED=true`、`AUDIO_ENABLED=true`、`CAREER_ALLOW_PAID_PROFILES=true` 保持不变。
- **公网验收**：`/api/health`、首页、职位库和浏览器助手教程均返回 `200`；匿名面经树与招呼语生成接口均返回 `401`。线上主 JS/CSS 已确认包含逐岗触发文案、面经公司/岗位编辑、上传入口和 Skill/面经高亮样式。
- **扩展验收**：`find-job-boss-helper-v0.2.7.zip` 返回 `200`，线上与本地文件均为 `15249` 字节，SHA-256 均为 `59E3DDE5133F172621EEBB3A3DE811CA63E784F793F5EE4D1CDCD1C6B81C9172`。
- **外部调用边界**：本次未主动调用付费模型、未触发微信草稿、Seedance 或 TTS 任务，也未使用真人 BOSS 账号发送招呼语。

## 2026-08-27 全量生产部署记录

- **发布目标**：发布求职助手上下文治理与用户记忆停用、PC 响应式首道门禁、全网公开面经采集、小红书浏览器采集、登录首页和面经库交互更新，以及浏览器助手 `0.2.9`。
- **发布版本**：业务代码提交 `e7ff69b`；生产仓库从 `e3a169f` 快进更新。本机 `.agents/skills`、个人 `data/`、临时测试文件、Playwright 产物、重复 pnpm 锁文件和旧版扩展 `0.2.8` 未进入发布。
- **发布前验证**：Python `368 passed`，WebUI `184 passed`，浏览器扩展 `28 passed`，Playwright Chromium/Firefox 响应式验收 `7 passed`；Vite production build、部署设置、Compose 配置、扩展语法、公开面经与小红书编排合约均通过。
- **部署调用链**：生产 Compose 配置展开通过 → `git pull --ff-only origin main` → `docker compose ... up -d --build` → `skill-seed` 与 `career-migrate` 退出码均为 `0` → API、Worker、Scheduler、Web 全量重建完成。
- **数据库与运行状态**：Alembic 从 `20260825_21` 连续迁移到 `20260827_26 (head)`；API、Web、PostgreSQL 均为 healthy，Worker、Scheduler、Caddy、Docling、Gotenberg 正常运行；Scheduler 已注册下一次周五 08:00 生产与 09:00 草稿任务。
- **Firecrawl**：生产 `.env.production` 已以不回显方式注入 `FIRECRAWL_API_KEY`，API 容器读取成功；本地与生产容器各执行一次最小 `/search` + `/scrape` smoke test，均返回 `public_web_firecrawl_smoke_ok`，未写业务数据库。
- **公网验收**：健康接口、首页、求职助手、面经库、职位库、扩展教程和 `0.2.9` ZIP 均返回 `200`；匿名记忆与公开面经任务接口均返回 `401`。线上扩展为 `21948` 字节，SHA-256 为 `060FDB174E668B0E15B461DB176FF77871EC71A4760FCE4F6424B15904528D0F`，与本地一致。
- **外部调用边界**：除上述两次 Firecrawl 最小验证外，未调用聊天模型、未触发微信草稿、Seedance、TTS 或真人 BOSS 招呼语发送。

## 2026-08-27 动态内容全量生产部署记录

- **发布目标**：发布动态 Top N 的 N+1 周榜文章编排、逐项目视觉简报与图片数量、自适应模型上下文容量、管理台图片/视频/语音开关，以及 PC 端归档原文、图片预览和 API Key 显隐交互。
- **发布版本**：业务代码提交 `51446e6`，生产仓库从 `84988ba` 快进更新；本地插件 Skill、个人 `data/`、临时 `src/test.py`、重复 pnpm 文件、Playwright 产物和旧版扩展 ZIP 未进入发布。
- **发布前验证**：Python `395 passed`，WebUI `195 passed`，浏览器扩展 `28 passed`；Vite production build、变更契约脚本、部署设置、Alembic 单一 head、源码编译、补丁密钥扫描与 Compose 配置均通过。
- **部署调用链**：推送 GitHub `main` → 生产 `git pull --ff-only origin main` → `docker compose ... up -d --build` → `skill-seed` 与 `career-migrate` 退出码均为 `0` → API、Worker、Scheduler、Web 全量重建完成。
- **数据库与运行状态**：Alembic 从 `20260827_26` 升级到 `20260827_27 (head)`；API、Web、PostgreSQL 均为 healthy，Worker、Scheduler、Caddy、Docling、Gotenberg 正常运行；Scheduler 已注册次日周五 08:00 生产与 09:00 草稿任务，启动日志未出现 `ERROR`、`Traceback` 或 `Exception`。
- **Skill 持久卷**：`skill-seed` 按既有规则跳过 57 个已存在 Skill；哈希确认 `github-project-blog` 持久副本与上一版种子完全一致且没有线上编辑后，仅将该文件更新到本次种子，更新后两者 SHA-256 均为 `EE675749BF08783A1EE51E29FC25A81E4C7610CE3256EDD92B7B122425DF8997`。
- **公网验收**：健康接口与首页、求职助手、面经库、职位库、工作台均返回 `200`；匿名会话和面经树接口均返回 `401`。线上主资源 `/assets/index-BG2ekzHc.js` 已确认包含“显示 API Key”“文章原文”“图片生成”。
- **生产开关与外部调用边界**：API 的 `VIDEO_SUBMIT_ENABLED`、`AUDIO_ENABLED`、`CAREER_ALLOW_PAID_PROFILES`、`PLATFORM_AUTH_REQUIRED`、`CAREER_REDACTION_ENABLED` 以及 Scheduler 的音视频开关均保持 `true`；本次未主动调用聊天模型、Firecrawl、微信草稿、Seedance、TTS 或真人 BOSS 发送动作。

## 2026-08-28 全量生产部署记录

- **发布目标**：发布技术文章确定性图稿规格、HTML 模板与 Gotenberg 截图链路，面经创建者归属、管理员维护与删除能力，旧默认求职历史归属修复，登录后认证路由刷新修复，Provider 实际模型标识留存，以及求职对话自动滚动、公开面经采集上限和审核台生成方案展示更新。
- **发布版本**：业务代码提交 `60253cd`，同时包含确定性图稿设计与实施文档提交 `d4789e0`、`e024079`；生产仓库从 `38534fe` 快进更新。个人 `data/`、本机新增插件目录、临时 `src/test.py` 和旧版扩展 `0.2.8` 未进入发布。
- **发布前验证**：Python 全量 `517 passed`，WebUI 全量 `205 passed`，浏览器扩展全量 `28 passed`；Vite production build、求职助手部署设置、Alembic 单一 head、受控源码编译、补丁凭据扫描与生产 Compose 配置均通过。
- **部署调用链**：推送 GitHub `main` → 生产 `git pull --ff-only origin main` → `docker compose --env-file .env.production -f docker-compose.production.yml up -d --build` → `skill-seed` 与 `career-migrate` 退出码均为 `0` → API、Worker、Scheduler 与 Web 全量重建完成。
- **数据库与运行状态**：Alembic 从 `20260827_27` 连续迁移到 `20260828_30 (head)`；旧默认 Actor 的求职会话剩余 `0` 条，固定管理员仍为唯一有效管理员；两张面经表的创建者字段与模型用量表的 Provider 模型字段均已落库。API、Web、PostgreSQL 均为 healthy，Worker、Scheduler、Caddy、Docling、Gotenberg 正常运行，启动后日志未出现 `ERROR`、`Traceback` 或 `Exception`。
- **确定性图稿与 Skill 持久卷**：API 镜像内字体文件和确定性渲染模块加载通过，媒体生产 readiness 无 blocker。`github-project-blog` 与 `grill-me` 的线上持久副本均与上一版种子哈希一致，确认没有线上编辑后才同步到新种子；同步后 API 与 Scheduler 读取哈希一致。
- **公网验收**：健康接口返回 `career_runtime_revision=2026-08-28-interview-admin-permissions-v2`；首页、求职助手、面经库和审核台均返回 `200`，匿名面经树、删除面经与创建公开采集任务均返回 `401`。线上主资源 `/assets/index-M0V9WIBS.js` 已确认包含图片总结方案、实际调用模型、面经删除、维护权限与默认 10 条采集文案。
- **生产开关与外部调用边界**：API 的 `VIDEO_SUBMIT_ENABLED`、`AUDIO_ENABLED`、`CAREER_ALLOW_PAID_PROFILES`、`PLATFORM_AUTH_REQUIRED`、`CAREER_REDACTION_ENABLED` 以及 Scheduler 的音视频开关均保持 `true`；本次只执行无付费请求的 readiness 检查，未主动调用聊天模型、Firecrawl、微信草稿、Seedream、TTS 或真人 BOSS 发送动作。

## 2026-09-02 全量生产部署记录

- **发布目标**：发布线上笔试助手及多平台 Capture Contract V2、面试大师末句识别与串行回答、模型动态输出上限、工作流实时日志、确定性文章图稿增强、微信标题处理、求职对话切换性能修复，以及 PC 端品牌、导航和管理台更新。
- **发布版本**：业务代码提交 `4e39297`，生产仓库从 `284e8ce` 快进更新；本机 `data/`、本机插件目录、临时 `src/test.py` 与扩展 `0.2.8/0.3.x` 旧包未进入提交或生产镜像。
- **发布前验证**：Python 全量 `616 passed`，WebUI 全量 `231 passed`，浏览器扩展全量 `44 passed`；Service Worker 与扩展脚本语法检查、Vite production build、API/Web Docker 镜像构建、求职助手部署设置、Alembic 单一 head、受控源码编译、暂存差异检查、常见凭据格式扫描和生产 Compose 配置均通过。
- **部署调用链**：推送 GitHub `main` → 生产 `git pull --ff-only origin main` → 真实 `.env.production` 配置展开通过 → `docker compose --env-file .env.production -f docker-compose.production.yml up -d --build` → `skill-seed` 与 `career-migrate` 退出码均为 `0` → API、Worker、Scheduler 与 Web 全量重建完成。
- **数据库与运行状态**：Alembic 从 `20260828_30` 连续迁移到 `20260901_33 (head)`；API、Web、PostgreSQL 均为 healthy，Worker、Scheduler、Caddy、Docling、Gotenberg 正常运行，发布后十分钟内 API、Worker、Scheduler 和 Web 日志均未出现 `ERROR`、`Traceback`、`Exception` 或 `CRITICAL`。Scheduler 已重新注册周五 08:00 内容生产和 09:00 草稿任务。
- **公网验收**：健康接口、首页、求职助手、线上笔试助手、面经库、职位库、工作台和浏览器助手教程均返回 `200`；匿名线上笔试分析接口返回 `401`。线上主 JS 已确认包含“线上笔试助手”“查看日志”和“校正答案”。
- **扩展验收**：`find-job-boss-helper-v0.4.0.zip` 返回 `200`，线上与本地文件均为 `29322` 字节，SHA-256 均为 `B1175D8D25BCED812FE4BAA5DBF47867B6AE63E83F243681E946F183A632F078`。
- **生产开关与执行边界**：API 的 `VIDEO_SUBMIT_ENABLED`、`AUDIO_ENABLED`、`CAREER_ALLOW_PAID_PROFILES`、`PLATFORM_AUTH_REQUIRED`、`CAREER_REDACTION_ENABLED` 以及 Scheduler 的音视频开关均保持 `true`；媒体 readiness 无 warning 或 blocker。生产仍未配置 `CAREER_PISTON_BASE_URL`，且没有独立 Piston 执行节点，因此线上笔试的题面分析、答案生成和归档代码已上线，运行代码能力继续按设计返回执行器不可用；未在承载应用和数据库的主机上启动 privileged Piston 容器。
- **外部调用边界**：除 Scheduler 启动时刷新 4 个公开 GitHub Star 快照外，本次未主动调用聊天模型、Firecrawl、微信草稿、Seedream、TTS 或真人 BOSS 发送动作。

## 2026-11-09 RackNerd 生产迁移

### 目标与发布边界

旧腾讯云竞价实例被回收后，用户明确授权投产至 RackNerd 新机，并选择全新数据库、沿用上次生产业务提交 `4e39297`。本次未发布当前工作区未提交功能，也未复制本地开发数据库或历史个人业务数据。

- 域名：`xingxingtech.cn`；新公网 IPv4：`64.188.4.240`。
- 主机：Ubuntu 24.04、6 vCPU、5924MiB 内存、约 96GiB 系统盘；原机自带 3GiB Swap。
- 应用目录：`/opt/wechat-agent-platform`；Docker Engine `29.9.3`、Compose `v5.7.0`。
- 主机 SSH 指纹：`SHA256:PlzYz64cShhuC4pI5+TB5ceDHetcPEXaxxQCJxVU4bQ`。

### 镜像与资源取舍

API 复用 2026-09-02 的已构建镜像作为来源，去除桌面构建产物和检索缓存后重新打包为 `wechat-agent-career-api:release-4e39297`。391 个运行文件与固定提交一致，精简前后的 Python 依赖完全一致。Web 保留同日构建内容，标记为 `wechat-agent-career-web:release-4e39297`。业务代码未重新构建，发布包约 154MiB。

调用链继续为 HTTPS → Caddy → Web/API → PostgreSQL；Worker 和 Scheduler 共用业务卷，Gotenberg 与 CPU Docling 留在容器私网。服务器只公开 22、80、443，数据库不映射公网。

运行时叠加 `docker-compose.capacity.yml`：聊天全局和单 Worker 并发均为 2；Docling 单 Worker、单线程、最多 2 vCPU、4GiB 容器内存。其他常驻服务分别设置内存上限。该配置用于十几名用户的低并发起步，不代表并行 OCR 或高并发模型吞吐已经验收。

日常查看或启动应同时使用两个 Compose 文件：

```bash
cd /opt/wechat-agent-platform
docker compose --env-file .env.production -f docker-compose.production.yml -f docker-compose.capacity.yml --profile document-processing ps
docker compose --env-file .env.production -f docker-compose.production.yml -f docker-compose.capacity.yml --profile document-processing up -d --no-build --pull never
```

### 数据、配置与验收

- 新数据库迁移至 `20260901_33 (head)`，`career-migrate`、`skill-seed` 均退出 0；7 个常驻容器运行，API、Web、PostgreSQL 健康，重启计数和 OOM 均为 0。Scheduler 在启动验收后按用户要求暂停。
- 重建固定邮箱管理员，完成真实 HTTPS 登录、认证查询、11 个导航模块、空会话与面经库、Skill 查询及退出验证。初始管理员密码单独保存在操作者本机仓库之外，不写入本文或生产日志。
- 新建 DeepSeek V4 Flash/Pro 两个组织模型档案，状态均为 `ready`。从新机读取官方模型目录返回 200；官方仍兼容旧 `deepseek-v4-flash` 标识。没有发起付费生成请求。
- DNSPod 权威解析及阿里、腾讯公共 DNS 均返回新 IP；Caddy 已签发 Let's Encrypt 证书，普通系统代理访问域名健康接口成功。证书有效期至 2027-02-07。
- 首页、求职助手、线上笔试入口、面经库与主 JS/CSS 返回 200；匿名认证及会话查询返回 401。扩展 0.4.0 为 29322 字节，SHA-256 与上次发布记录一致。
- 实际 6GB 云机常驻全部服务时，5 页合成中文扫描 PDF 解析成功，耗时 56.11 秒；Docling 累计内存峰值约 2883MiB，无内存上限事件或 OOM。测试后整机可用内存约 2680MiB、磁盘可用约 76GiB。
- 邮件发件域为 `mail.xingxingtech.cn`，邮件记录保持不变；未主动发送邮件验证。
- 音视频与付费档案开关沿用旧生产记录为 true，媒体 readiness 无 warning/blocker；未创建音视频或微信草稿。
- Scheduler 启动验收时曾注册周五内容与草稿任务，仅刷新公开 GitHub Star 快照；用户选择暂不使用公众号功能后已停止容器，并在运行覆盖文件中归入 `wechat-pipeline` profile，默认启动命令不再启用它。

### 后续边界

公众号 token 初次验证返回 `40164`，用户明确选择稍后处理；公众号定时任务保持暂停。后续先在公众号 API IP 白名单新增 `64.188.4.240` 并验证授权，再显式启动 `pipeline-scheduler`。Piston 沿用上次生产边界，未部署在承载主应用和数据库的服务器上。未恢复旧生产历史数据，也未执行完整业务高并发、付费模型生成或邮件投递验收。

### 同日追加：指定本地业务数据导入

用户随后明确要求将本地公众号文章、管理员身份、面经和 Skill 迁移至新生产，此授权更新初次迁移时的空库范围。此次迁移来源为本地数据，不代表恢复了旧腾讯云实例的数据。

- **范围与取舍**：云端继续使用业务提交 `4e39297`、数据库版本 `20260901_33`；本地数据库版本为 `20261109_37`，核验此次涉及的 5 张面经表结构完全一致后限定表导入。没有整库覆盖、应用构建或镜像更新。
- **调用链**：只读导出指定本地数据 → 规范化 Windows 图片路径至 `/app/outputs` → 可移植 Skill 导出 → SSH/SFTP 传输并核对 SHA-256 → PostgreSQL 与 SQLite 事务预演回滚 → 文件入持久卷与事务导入 → 数据库逐字段/文件哈希复核 → 正式 HTTPS 业务验收。
- **公众号**：24 篇文章、9 份排版、9 条历史草稿记录、10 条内容审核记录、142 条图片索引；图片去重后 68 个文件约 33.16MB。关联数据为 19 条周榜、15 个仓库及 103 条 Star 快照。迁移草稿记录不调用微信，不生成或发送新草稿。
- **管理员及面经**：沿用云端现有管理员邮箱、ID 和密码，保持 admin 角色；34 份面经及创建者归属映射到该管理员，同时导入 16 家公司、564 个已有文本切片、29 条来源及 19 份已导入网页元数据。本地切片没有 Embedding 向量，云端继续使用已验证的关键词检索。
- **Skill**：迁移 116 个本地 Skill，随附只读 references 和许可证；保留已有云端种子，合并后列表可见 136 个。沿用已有可移植方案，不携带本机脚本、插件运行时、缓存或凭据。
- **验证**：24 篇文章与 34 份面经的 HTTPS 正文逐条一致，34 份面经的管理员编辑权限均成立；116 个迁移 Skill 全部显示，3 个 Skill 正文与 3 张图片 HTTP 哈希抽查通过；关键词搜索返回 3 条结果。数据库逐字段、外键和全部迁移文件 SHA-256 校验通过。
- **开销与边界**：压缩传输包 34,492,828 字节，图片和 Skill 文件合计约 36.68MB，另有少量数据库数据；使用现有 VPS 磁盘和套餐流量，无新增云资源，也没有模型、Embedding、微信或音视频调用。未迁移聊天、简历、普通账号、模型凭据、任务队列、执行日志或音视频文件；公众号 Scheduler 继续停止。
- **最终状态**：面经树显示全部 34 份记录；API、Web、PostgreSQL 健康且重启次数均为 0，镜像 ID 与导入前一致。云端仍只有 1 个管理员账号，聊天、简历档案与 Agent 任务数量均为 0。
- **临时数据**：已清理云端容器/主机的传输文件、本机压缩包和含文章/面经正文的 payload；数量、文件哈希清单与验收结果不含业务正文和凭据，本机临时目录保留可移植 Skill 提示词导出。实施记录见 [`2026-11-09-selected-local-data-migration.md`](superpowers/plans/2026-11-09-selected-local-data-migration.md)。

### 同日追加：注册账号、模型与私密应用配置同步

用户反馈旧邮箱无法登录，并明确要求同步模型 API Key 和各类应用私密信息。上一次限定数据迁移未包含普通账号或本地密码；此次已按扩展授权补齐本地全部 3 个注册账号。唯一管理员仍为固定邮箱，保留原云端 ID 及面经归属，密码散列和用户名同步为本地原值；另 2 个账号保持普通用户角色。登录应使用各邮箱的本地原密码，初次投产随机密码已经失效，原临时密码文件已删除。仅在旧腾讯云注册、未留在本机的账号不在本次可恢复范围内。

模型档案按 profile key 或 provider/model 匹配现有连接，保留云端连接 ID 后导入本地属性；3 组 Key 在本机内存解密，通过 SSH 标准输入传给云端，再以现有云端主密钥重新加密。DeepSeek V4 Flash、DeepSeek V4 Pro 与 Qwen 3.6 Flash 均显示 `ready`，数据库不保留明文 Key。

GitHub、DeepSeek、火山方舟、豆包 TTS、百炼、公众号、LangSmith、固定文档视觉、Resend、Firecrawl、邮箱验证码签名密钥及配套地址合计 14 项配置已与实际 API 进程逐项核对一致。大部分服务凭据在首次投产时已经写入；此次仅邮箱验证码签名密钥需更改环境值。生产数据库口令、域名和服务内网地址、凭据加密主密钥保持云端配置，当前管理员流水线配置同步为本地生效版本。

验收包含生产事务预演回滚、正式写入与密码散列一致性核对、模型密钥重加密/解密一致性、3 个账号短期会话的认证 API 和角色检查（检查后撤销会话），以及模型设置页 3 项 `ready` 状态和文章/面经访问。未读取或导出原密码明文，也未进行真实密码输入、邮件投递或付费模型生成验收。为加载环境变更，仅用原镜像重新创建 API/Worker，未构建或发布应用代码；Scheduler 继续停止，正式域名健康接口返回 200。云端临时同步脚本已清理，验收记录不包含 Key 或密码散列。

### 同日追加：求职助手历史会话

用户反馈求职历史尚未同步，已按本次扩展授权补充迁移。来源为 2026-11-09 20:14:12（Asia/Shanghai）的本地一致性快照，SSH 标准输入传输业务数据，不创建正文传输文件。账号按邮箱映射、模型按 profile key 映射到云端既有 ID，职业空间按用户及规范化名称复用；保留云端原有会话，历史数据以 UUID 追加并校验，不整库覆盖。

- 导入 54 个会话、194 条消息、108 个轮次、6 份简历解析资料、18 份岗位资料、17 份已有匹配结果、26 条历史上下文绑定、407 条轮次事件和 22 条历史模型用量。管理员名下 51 个历史会话（49 个未归档、2 个已归档），网易普通账号名下 3 个；原有云端会话保留，生产共 55 个会话，管理员默认列表显示 50 个未归档会话。
- 生产仍为 `4e39297` 与数据库 `20260901_33`。本地新增的 `messages.ui_payload`、岗位搜索批次表与旧生产不兼容，因此 4 条卡片以可读文字展示条件和岗位结果，原卡片结构及 5 个批次/岗位版本快照保存在现有轮次 JSON 元数据中。没有新增表或上线卡片交互；将来发布新版功能若需恢复结构化交互，应从这些快照回填。
- 本地 1 条 8 月遗留 running 轮次按历史中断转为 cancelled，清理旧租约；未恢复执行槽、待执行任务、记忆或压缩队列，未触发历史推理。目标数据库自行分配历史轮次队列序号和事件 ID，避免与现有自增序列冲突。
- 云端事务预演回滚、正式导入和逐字段验证通过；生产仓储逐条复核 194 条消息、4 条卡片兼容数据及 11 个当前上下文，3 个账号历史列表与跨账号隔离通过，2 个详情 HTTP 抽查通过。详情接口对完整简历/JD 可能触发后台评估，因此该部分只读核验，未主动调用付费模型。历史模型用量仍为导入的 22 条。
- 生产健康接口 200，API healthy、重启计数 0，原镜像 ID 不变；临时同步工具已清理，验证会话已撤销，记录仅保留数量、ID、归属与哈希，不含正文或凭据。此次为一次性本地历史迁移，不恢复旧腾讯云独有数据，也不持续同步本地后续修改。

## 2026-11-10 本地更新投产

用户明确授权“检查本地更新，同步到生产并投产”。此次发布当前工作区运行文件，包含对话求职、搜索批次、自动推荐、BOSS 收件及执行层、简历与岗位资料、页面布局和扩展更新；排除本机数据库、生成产物、真实 `.env`、临时目录与桌面构建物。

### 发布标识与镜像

- Git HEAD 为 `3215973a9e6ac9d40f0284813a2f7a2dca735ad0`，功能仍为未提交工作区修改；本次没有 Git 提交或推送。
- 固定工作区 SHA-256：`baa5f320b8733d87cc70cadb35abd74f57cf5f8c10f8f93f85717d99ffbe52b4`，标签 `release-local-baa5f320b873`。构建及上传后均再次核对运行文件未变化。
- API：`wechat-agent-career-api:release-local-baa5f320b873`，ID `sha256:5a8fcc1f2f0e8a7802709e9b2f080af10e9fc2b8fe7b34727ba7d0d57d78afe6`。
- Web：`wechat-agent-career-web:release-local-baa5f320b873`，ID `sha256:2a5b31c758d9e243604899f70fe9f81e677c2b0db793e8765a86df00bdeb3fd6`。
- 上传包 174,857,377 字节，SHA-256 `fe6bc24358c55ac74b7beae190d9b0537ec6437ab90edfb2317e182893d612f6`；生产目录 `/opt/wechat-agent-platform/releases/release-local-baa5f320b873`。
- API 复用既有 Python 依赖，Web 使用本次 Vite 构建。健康接口的 `career_runtime_revision` 仍是历史兼容标识，本次代码版本以工作区 SHA、实际镜像 ID 和静态产物校验为准。

### 发布前修复和验证

修复旧幂等回执降低客户端 revision、自动推荐覆盖原数量/偏好、分页与普通聊天无效读取 JD。普通聊天不还原岗位正文，岗位分析继续保留跨批/公司别名解析，不在本次扩展性能重构。新增兼容历史回填 CLI `scripts/restore_migrated_job_search_history.py`，默认事务预演，`--apply` 才提交，重复执行保留已存在的新 UI 和批次。

Python 893 项、Web Node 371 项、扩展 Node 99 项通过；Chromium 首轮 112 项通过，3 项因原测试固定 5173 端口而失败，设置独立测试入口后这 3 项补验通过。Vite build 成功，保留大 chunk 提示。真实新 API/Web 镜像连接独立本机 PostgreSQL 完成合成管理员密码登录、认证与新路由、Skill 及扩展下载验证；临时容器和网络清理完毕。没有将这些 Mock/合成检查标成真实招聘平台验收。

### 投产顺序与数据保留

上传并校验镜像 → 停止 API/Worker → 更新现有容量覆盖文件镜像标签 → 记录原数据计数/指纹 → Alembic 从 `20260901_33` 升至 `20261109_37` → 兼容回填预演和提交 → Skill 缺失种子初始化 → 比对数据指纹及 `.env.production` 哈希 → 启动 API/Worker/Web/Caddy → 生产认证与公网验证。

原有账号、密码散列、模型档案及加密 Key、会话/上下文、面经、公众号文章与历史、Skill 正文指纹保持一致；4 条此前转文字兼容的消息恢复原文及 `ui_payload`，并恢复 5 个搜索批次和 44 条岗位版本。保留 55 个会话、194 条消息、24 篇公众号文章、34 份面经、136 个 Skill、3 个账号、3 组 `ready` 模型连接，未覆盖云端新建会话。生产 `.env.production` 哈希一致，6GB 运行参数、原密码及 API Key 继续使用。

### 生产结果与使用边界

- 3 个账号的认证 API、角色、历史列表和跨用户拒绝通过；逐条核验 194 条原始消息正文，4 条结构化岗位卡片可读取。管理员默认列表 50 个未归档会话，网易普通账号 3 个，Gmail 账号 0 个；原归档状态保留。
- 正式 HTTPS 健康接口和新增 SPA 路由返回 200；HTML 引用的主 JS/CSS 与本地构建哈希一致，匿名会话 API 返回 401；API/Web/PG healthy，API/Worker/Web/PG 重启计数均 0，无 OOM。
- 扩展版本 **0.6.1**，下载 `/downloads/find-job-boss-helper-v0.6.1.zip`，46,322 字节，SHA-256 `6ee4392bc52192ec5202cef85d1d4c8393ec145411e581a7316df8d9944bad98`。网站发布不会自动升级用户浏览器中的扩展，需要重新加载新包。
- Scheduler 继续停止，未执行微信、邮件或付费模型调用；历史用量仍为导入的 22 条，活跃历史任务 0。真实 BOSS 近期消息接收、分页/限流和逐条确认发送尚未在真实账号上验收；Piston 边界沿用既有配置。
- 隔离测试库已删除，本机用户 Worker 保持运行；只清理本次合成数据、验证会话和分段上传文件。报告与复跑记录见 [发布回归报告](regression-report-2026-11-10-production-release.md)。

## 2026-11-10：Codex / Claude Code 共用发布技能

用户要求将本地提交、推送、投产与仅重启封装为可复用技能。本体位于 `C:/Users/ccDarling/.codex/skills/wechat-release`，Claude Code 的 `C:/Users/ccDarling/.claude/skills/wechat-release` 是指向本体的目录链接。此技能没有写入生产 Skill 种子库，服务于开发机发布操作。

- **调用**：Codex 使用 `$wechat-release 检查本地更新，提交推送并投产`，Claude Code 使用 `/wechat-release 检查本地更新，提交推送并投产`。仅重启使用 `/wechat-release 仅重启生产，不发代码`。仍遵守当次用户授权；创建技能不授权实际发布。
- **实现与取舍**：入口 `release.py` 调用 `local_ops.py` 完成具体文件清单提交、普通 Git 推送、隔离测试库检查和固定提交镜像包，再经标准 OpenSSH/SCP 调用服务器标准库脚本 `server_ops.py`；共用约束在 `common.py`。不依赖 Codex MCP、Paramiko 或 `tmp` 临时工具，也不添加备份体系。目标及公开主机 Key 放在 `references`，私钥仍只在本机 `~/.ssh/xingxingtech_prod.pem`。
- **发布约束**：先检查、再提交、固定 HEAD 构建、普通推送、上传并校验、比较既有挂载/数据库连接、暂停 API/Worker、迁移与缺失种子初始化、切换应用、验证镜像 ID/数据库 head/HTTPS/扩展下载。保留生产环境和持久卷，核心计数不能减少；不重跑历史数据导入。Scheduler 保持暂停。迁移失败后停止，不自动降级数据库或宣称已回滚。
- **仅重启**：只对现有 API/Worker/Web/Caddy 执行 Compose restart，核对容器与镜像 ID 不变；不提交、推送、打包、上传、构建、拉镜像、迁移或 up，不重启数据库和 Scheduler。
- **本次验证**：技能结构验证通过，22 项离线回归通过；另一次独立离线演练覆盖选择性提交、固定提交包、仅重启及失败停止，并发现和修复“只比较配置文件，遗漏实际容器卷漂移”的问题。已用 Claude 入口运行计划预览，并确认本机 Claude Code 版本 2.1.207、SSH 私钥可离线解析和公开指纹匹配。本次没有提交/推送项目代码，也没有连接或修改生产。
- **当前依赖边界**：Docker Desktop 的 Linux engine 当前未运行，`doctor` 如实返回 `partial`。下一次完整检查/构建先启动 Docker Desktop 和本机 PostgreSQL，再运行 `doctor`、`check`；仅重启不需要本机 Docker。此次模拟测试不能代替下一次真实发布的应用与业务验收。当前生产仍是上一节的 `release-local-baa5f320b873`。
