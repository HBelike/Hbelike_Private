# 本地更新发布回归报告

## 结论与范围

本地工作区快照 `baa5f320b873` 已投产，数据库升级到 `20261109_37`，生产只读验证及数据保留检查通过。自动化与合成镜像验收通过；真实 BOSS 收发、邮件、微信和付费模型业务闭环未执行，不将这些范围认定为已验收。

Git HEAD `3215973`，包含大量原有未提交和未跟踪功能；本次没有重置、提交或推送用户改动。按当前运行文件哈希冻结发布快照，构建、上传与投产前均复核未变化。依据为 `AGENTS.md`、本次明确投产授权及项目回归技能；没有使用 Superpowers 预检。

## 本次确认并处理的问题

| 问题 | 处理与验证 |
| --- | --- |
| P1 旧操作回执/晚到响应使前端批次版本倒退 | 保持当前版本单调前进，旧状态不覆盖新批次；原操作回执和显式历史查询语义保留。修前新增 Node 用例失败，修后通过 |
| P2 自动推荐固定“最多三个”，未显式继承原数量/偏好 | 内部任务包含原始用户需求，原请求未指定数量才默认三；真实隔离库核对入队任务保留“推荐10个”和“应届生” |
| P2 分页、冻结及普通聊天多读 JD | SQL 分页仅多取一条，计数/预览存在性用聚合/EXISTS，普通聊天 Worker 不还原岗位正文；真实 PG 及 Processor 用例验证。岗位分析分支仍完整还原冻结批次以保留别名/跨批语义，未重跑压力阶梯 |
| 历史文字兼容消息在新版无法还原卡片/批次 | 新增默认回滚预演的回填 CLI，从已有 JSON 快照恢复原文/UI/批次及岗位版本；真实事务和重复执行用例通过，生产恢复 4/5/44 条 |

## 自动化及镜像验证

| 检查 | 结果 | 证据与边界 |
| --- | --- | --- |
| Python 全套 | **893 passed**，27 warnings | `tmp/production-release-20261110/python-release-final.log`；Fake/Mock 与真实隔离 PG 混合，不是真实 Provider 推理 |
| Web Node | **371 passed** | `web-unit.log`；包含批次回退、布局/状态及桥接逻辑 |
| 扩展 Node | **99 passed** | `extension-unit.log`；源 DOM/执行逻辑替身，不是实际 HR 送达 |
| Chromium | 首轮 **112 passed / 3 failed**；补验 **3 passed** | 首轮 3 项固定旧端口而连接失败；设置 `CAREER_CARD_TEST_URL` 后定向复跑通过，未改业务断言。日志 `browser-all-fixed.log`、`browser-card-port-fixed.log` |
| Vite build | 通过 | `web-build.json` exit_code=0，日志保留大 chunk 提示 |
| 生产镜像导入/启动 | 通过 | 无网络导入新模块；新 API/Web 镜像连接隔离数据库，真实合成管理员密码登录、认证/新路由/Skill、SPA 回退和扩展 ZIP 哈希通过，见 `image-smoke-result.json` |
| 迁移链 | 通过 | 全套真实本机测试覆盖旧基线 33→35→当前 head 并保留旧消息；专用测试库初始化 37，未迁移开发业务库 |

浏览器包含 PC 功能与既有手机兼容；未进行平板专项、真实手机硬件、桌面客户端重新打包或新的 32 并发压测。115 项浏览器检查均使用独立 5187 前端和 API/扩展 fixture；未被 fixture 接管的请求代理到关闭端口，不接入用户本机后端。

## 首轮失败与测试隔离处理

修前定向测试复现 Node 版本回退和 3 项真实 PostgreSQL 失败。首次 PG 测试误用正在运行的开发数据库队列，一条合成内部任务被本机 Worker 领取，导致 fixture 清理时租约约束失败。随后确认该轮次已 failed、无槽位占用，仅删除本轮确定的 `job-search-qa-d26d56a8-002a-4b4a-9ec9-1c02d91c8e82` 合成组织；没有清空用户队列、领取其他任务或停止用户 Worker。

后续所有发布 PG 检查使用独立数据库 `codex_production_release_*`，临时库 URL 只存在进程环境，测试 Provider Key 置空且 LangSmith tracing 关闭。新增普通聊天测试先后出现测试自身缺 import、未模拟 Worker 领取状态的失败，已修正测试准备，最终完整 893 项通过；保留首轮日志，没有删除断言或修改产品预期。

检查工具另修正 Windows `.cmd` 路径及终端编码问题；Vite 实际构建早已成功，终端打印 Unicode 失败不作为产品构建失败。所有检查日志和 JSON 位于 `tmp/production-release-20261110/`，不包含连接密码或 API Key。

## 模块验收与限制

| 模块 | 本轮实际验证 | 未覆盖 |
| --- | --- | --- |
| 登录/账号/管理 | 隔离镜像真实密码登录；生产 3 账号短期会话认证、角色、隔离；原密码散列指纹保持一致 | 未掌握或重输生产原密码、未发送邮件 |
| 求职/历史/上下文 | 真实 PG/Worker 测试模型；生产 194 条原文哈希、55 会话归属、6 简历与18岗位资料保留；4 卡片/5批次/44版本恢复 | 生产付费推理与真实招聘检索 |
| BOSS/扩展 | 99 项逻辑与 PC 浏览器合成收件/分页/暂停/接管/逐条确认；生产新 API、SPA 与 0.6.1 下载 | 真实账号新 HR 回复、源接口兼容及真实发送接受证据 |
| 简历/文档 | 全套 Python 和相关 PC/手机 fixture；既有解析资料保留 | 本轮未重跑生产 OCR 或云端视觉，未上传真人附件 |
| 面经/RAG | Python/浏览器；生产面经树可读，34 正文及564切片指纹不变 | 新采集、Embedding、付费推理 |
| 公众号/素材 | 24 篇文章及素材/排版/草稿指纹不变，生产历史可读 | 定时任务保持停止，没有微信、图片、音视频动作 |
| Skill/评测/监控 | 全套逻辑/浏览器，136 Skill 保留；当前模型3项ready | 真实评测实验、LangSmith UI、外部插件运行时 |
| 线上笔试/面试 | 后端测试与既有浏览器布局/状态用例 | 实际音频、桌面硬件和 Piston 远端执行；沿用既有边界 |

## 投产与生产证据

发布包 174,857,377 字节，上传后 SHA-256 一致；镜像标签 `release-local-baa5f320b873`。停止 API/Worker 后执行 34–37 迁移及历史恢复，生产配置文件哈希不变。原有 PostgreSQL、公众号 SQLite 和 Skill 正文指纹比对一致；消息中特别核验 4 条兼容消息恢复原文/UI，其余正文逐条一致。

生产保留 3 个账号、3 组 ready 模型、55 会话、194 消息、24 篇文章、34 面经及136 Skill。管理员活动会话 50，网易账号 3，Gmail 0；归档状态和跨账号隔离保留。临时验证会话已撤销。

正式 HTTPS 健康接口与新 SPA 路由通过，主 JS/CSS 与本次构建相同，匿名 API 返回401。扩展 0.6.1 为46,322字节，SHA-256 `6ee4392bc52192ec5202cef85d1d4c8393ec145411e581a7316df8d9944bad98`。API/Web/PostgreSQL healthy，API/Worker/Web/PG 均无 OOM、重启0；Scheduler exited，活跃任务0，历史模型用量仍22条。

见 `production-deployment-result.json`、`production-verification.json`、`release-manifest.json`。报告不以健康接口历史 revision 字符串代表当前代码版本，实际镜像 ID 与快照哈希才是本次发布依据。

## 复跑与清理

```powershell
.venv/Scripts/python.exe tmp/server-migration-20261109/run_release_checks.py init
.venv/Scripts/python.exe tmp/server-migration-20261109/run_release_checks.py run python-check -- .venv/Scripts/python.exe -m pytest tests -q --disable-warnings --tb=short
.venv/Scripts/python.exe tmp/server-migration-20261109/run_release_checks.py cleanup
```

隔离数据库、镜像验收容器及网络、生产短期验证会话、分段上传文件均已清理；本机用户 Worker 保持运行。构建/传输镜像、无业务正文的哈希清单及日志保留为本次发布证据，相关临时目录已在本机 `.git/info/exclude` 排除。

## 新任务交接摘要

1. 目标：检查本地更新并投产；已完成，生产快照 `baa5f320b873`、数据库37。
2. 已修改：批次控制器、自动分析、仓储分页/冻结、普通聊天 Worker；新增历史回填脚本和回归测试；更新设计、计划及部署记录。
3. 已验证：Python893、前端371、扩展99；浏览器112+3补验；真实镜像合成登录和生产只读验证通过。
4. 数据：原账号/密码/Key/文章/面经/Skill/历史保留，4卡片/5批次/44版本已回填，194消息原文一致。
5. 未解决：真实BOSS收发、付费Provider、邮件/微信和硬件闭环未验证；岗位分析分支仍全目录还原，未新增压力验证。
6. 关键命令：原 Compose 两文件继续使用；仅显式启用 `wechat-pipeline` 才运行公众号；未来构建须重新核对未提交工作区。
7. 下一步：用户更新浏览器扩展0.6.1；需要真实BOSS发送验收时，另确认具体招聘者和最终文本。
