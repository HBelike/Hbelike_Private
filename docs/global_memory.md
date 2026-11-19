# 新任务交接摘要

> 当前生产事实；最多12条，不记录密码、API Key、私钥或用户业务正文。后续授权仍按 AGENTS.md 和当次用户请求判断。

1. **目标**：本次“检查本地更新，同步到生产并投产”已完成；2026-11-10 发布工作区快照 baa5f320b873，数据库 20261109_37。
2. **生产**：RackNerd 64.188.4.240，域名 https://xingxingtech.cn，6 vCPU/5924MiB/96GiB；目录 /opt/wechat-agent-platform。旧腾讯竞价机已回收，不再作为部署目标。
3. **版本**：Git HEAD 3215973，功能仍未提交，未推送 Git；实际发布标签 release-local-baa5f320b873，完整源哈希及 API/Web 镜像 ID 见 tmp/production-release-20261110/release-manifest.json。不能用旧 HEAD 或健康接口历史 revision 代表当前发布。
4. **已修改文件**：修复 web-ui/src/job-search-session.js、src/career_assistant/job_search_analysis.py、persistence/job_search_repository.py、turn_worker.py；新增 scripts/restore_migrated_job_search_history.py 和相应回归测试；设计记录、发布计划、部署文档与回归报告均已更新。
5. **数据与凭据**：3 个本地注册账号及原密码散列、3 组模型连接、各应用 Key 已同步；管理员邮箱仍为2963613812@qq.com且ID保留。新机最初随机密码已作废，原临时密码文件已删除。真实凭据在生产 .env/数据库，仅用SSH传输，日志/文档无明文。
6. **业务保留**：55 会话、194 消息、24篇文章、34面经、136 Skill、6简历解析和18岗位资料；管理员50个活动会话，另2个历史会话保留归档。已回填4条卡片、5批次和44个岗位版本，194条消息原文哈希一致。
7. **已验证结果**：Python893、Web371、扩展99通过；Chromium112首轮通过，3个端口配置问题补验通过；实际镜像合成登录、新路由、生产3账号认证/隔离、静态资源、模型3项ready和HTTPS均通过；API/Web/PG健康，重启0/OOM0。
8. **运行边界**：公众号Scheduler停止并归入wechat-pipeline profile；未执行真实BOSS收发、微信、邮件或付费生成。Piston未新增部署，未做新32并发压测；岗位分析仍全目录还原以保留别名/跨批语义。扩展0.6.1需要用户重新加载，网站更新不会自动升级本机扩展。
9. **关键命令**：后续常规发布使用本机 `~/.codex/skills/wechat-release`，Claude Code 的 `~/.claude/skills/wechat-release` 链接到同一本体；`$wechat-release` 或 `/wechat-release` 可检查、提交推送、投产或仅重启。入口 `scripts/release.py`，配置 `references/target.json`；不再依赖 tmp 迁移工具。生产仍使用 production+capacity 两份 Compose。仅重启不会发代码或迁移。
10. **下一步与证据**：用户更新扩展0.6.1；真实发送需具体招聘者及文本确认。发布记录 docs/platform_production_deployment.md，报告 docs/regression-report-2026-11-10-production-release.md，临时构建/校验记录 tmp/production-release-20261110/。未来构建先核对当前未提交改动，不重置或覆盖用户数据。
11. **技能验证与边界**：发布技能结构校验及22项离线回归通过、Claude入口计划预览通过；部署前额外比较实际容器挂载/连接与候选配置，防止卷漂移。本次只创建技能和更新文档，没有提交推送或连接生产。Docker Desktop当前未运行，完整 check/prepare 前先启动并重跑doctor；仅重启只需SSH。离线模拟不代表真实生产发布已验收。
