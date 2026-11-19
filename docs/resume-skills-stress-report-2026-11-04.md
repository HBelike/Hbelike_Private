# 简历初评压测与双Skill接入报告

日期：2026-11-04。基准为本地当前工作区，未部署、未连接生产。报告区分数据库/Worker真实执行与模型替身，不用模拟失败率估算线上失败率。

## 结论

自动初评的重复确认去重、模型失败收尾和后续对话边界在本次有限压测中通过。仍有两项需要改进：高并发确认资料的尾延迟，以及已有Worker心跳数据库异常时的收尾问题。主对话真实测试还发现本地数据库未升级造成500，补齐已有迁移后恢复。

指定的[resume-builder](https://skills.sh/reactive-resume/reactive-resume/resume-builder)已安装到项目，和`tailored-resume-generator`都能在主对话显式调用。部署种子保留两者，并补齐schema参考和许可证；本地Linux容器验证通过。未集成Reactive Resume应用或MCP，未验证JSON在目标网站的实际导入。

## 自动初评压测

在本机PostgreSQL创建唯一命名的临时数据库，升级到`20261104_34`，创建24个合成Actor、会话与简历。使用真实绑定仓储、行锁、Skill Runtime、Turn payload、队列、Intake、Response Runner、两个Worker和持久化事件。仅模型响应为内存替身，没有付费并发请求。脚本不是HTTP负载机，不覆盖代理、登录或真实Provider吞吐。

每档8个会话，每会话重复确认8次；三档共192次请求。

| 并发 | 请求数 | 非预期异常 | 唯一初评Turn | P50 | P95 | 吞吐 |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 64 | 0 | 8 | 439.95ms | 472.30ms | 2.25/s |
| 8 | 64 | 0 | 8 | 2863.19ms | 3161.85ms | 2.85/s |
| 32 | 64 | 0 | 8 | 9868.47ms | 10244.00ms | 3.22/s |

结果：

- 192次重复确认最终只有24个自动初评Turn，未重复生成。
- 第一批24次模拟模型调用中，20次成功；另4次是主动注入的Provider失败，均落为`failed`及`error`事件，没有显示为成功，也没有终止其他任务。
- 产生100条delta、20条done和4条error事件；结束后占用槽位为0，模型替身实际峰值7，配置上限8。没有要求调度必须精确占满8，以免把线程时序误当失败。
- 同一简历的失败版本再次确认返回既有失败Turn，不自动重烧模型；自动初评不会被后续普通问题继承。
- 同一主对话再组合挂载两份Skill，完整通过一次额外Worker回复，随后可按原规则继承。自动与手动作用域分别验证。
- 整段探针约76.18秒，临时数据库已删除。探针最初的Actor字段名错误已纠正，不归为业务缺陷。

证据：`tmp/resume-skills-stress/stress.py`、`stress-result.json`、`stress.log`、`migration.log`。

### 延迟问题

32并发P95超过10秒，吞吐仅略有增长，当前本机开发环境的绑定链路不适合直接宣称高并发可用。一次调用包含Skill目录发现与正文构建，开发机还扫描用户和插件缓存，不能据此推断生产59个种子的同等延迟。

进一步对3次`CareerSkillRuntime.resolve()`做cProfile，总耗时1.584秒，其中`SkillLibraryService.list_skills()`累计1.271秒；目录扫描、路径解析及`get_skill()`再次扫描占据主要时间。证据：`catalog-profile.txt`。建议优先让重复初评在构建Skill Prompt前命中幂等结果，并给目录元数据做可失效缓存，再重测；没有证据要求先扩数据库或增加Worker。

### 心跳故障仍未修复

对当前Worker注入一次heartbeat仓储`ConnectionError`，仍复现异常传播、`finish_claim`调用0次。位置：`src/career_assistant/turn_worker.py`的`finally: await heartbeat_task`及心跳协程。证据：`heartbeat-fault.log`，复跑入口为`tmp/audit-20261104/worker-heartbeat-fault.py`。

这是数据库异常故障注入，不代表本轮PostgreSQL真实宕机。普通模型失败可以正确收尾，不代表数据库心跳失败也被处理。建议优先修这一已有P1，补租约丢失/数据库短暂不可用下的终态与调度测试；本轮按压测范围保留问题，未扩展修改Worker。

## 主对话真实验证与本地500

使用临时普通账号、合成简历和JD，经真实登录HTTP、Turn队列、Worker、DeepSeek和SSE验证：

| 命令 | 结果 | 耗时 |
|---|---|---:|
| `/resume-builder`，要求只审查结构和事实 | succeeded，Skill真实mounted，返回具体建议，无工具外调 | 3.657秒 |
| `/tailored-resume-generator`，要求审查岗位表达 | succeeded，显式替换上一Skill，返回具体建议，无工具外调 | 2.648秒 |

两次均为小额合成输入调用，不包含用户简历；测试账号、会话与会话数据已清理。证据：`live-chat-result.json`与`live_chat.py`。这是指定两条聊天调用的真实端到端证明，不是大量真实模型并发承载测试。

首次测试提交Turn返回500：当前仓储查询`messages.ui_payload`，本地数据库仍为`20260901_33`。核对已存在的迁移`20261104_34_chat_job_search.py`仅新增JSONB字段后，执行本地`alembic upgrade head`；现current=head=`20261104_34`，两条调用重测通过。未为此改动或回退其他工作区代码。

## Skill来源、适配与部署准备

- 来源仓库`reactive-resume/reactive-resume`，固定提交`19a4133e326b5f6b6ad09862b6b0f7b6002e1924`，路径`plugins/reactive-resume/skills/resume-builder`。[上游许可证](https://github.com/reactive-resume/reactive-resume/blob/19a4133e326b5f6b6ad09862b6b0f7b6002e1924/LICENSE)为MIT，完整文本已保留。
- 项目副本位于`.agents/skills/resume-builder/`，保留上游主要流程和`references/schema.md`，增加中文描述及当前运行能力说明。用户要求审查时不强制生成JSON、不重复索取绑定资料；岗位改写可与另一Skill共同挂载。
- 部署种子为`deploy/skill-seeds/resume-builder/`与`tailored-resume-generator/`。原种子工具只复制SKILL.md，现同包复制只读references中的md/json/txt和许可证，不复制脚本、CLI、缓存或凭证；默认不覆盖已有文件。
- `catalog.manifest.json`更新为59个Skill，对附带资源也记录路径和SHA-256；公开来源锁记录固定提交。全局用户安装版本未修改。
- 本地使用已有`wechat-agent-career-api:local`镜像，挂载当前源码与种子只读，`--network none`、无开发机用户Skill目录，临时安装后两份Skill均能发现/组合挂载，schema与LICENSE存在。日志`linux-seed.log`。这不是生产部署，也不是新的完整生产镜像构建。
- 当前主对话没有任意文件读取、Reactive Resume MCP或schema校验工具。附带参考随包保存不等于模型已读取150KB全文；JSON应标为待目标版本验证的草稿，不能声称已验证导入。安装Skill不隐式增加工具能力。

## 回归与改动

后端660项、前端252项通过，Vite构建成功（仍有已有主包体积提示）；便携性脚本及新增2项包迁移/组合挂载测试通过。`git diff --check`通过。

本轮修改：Skill包与部署种子、`src/services/skill_portability.py`、`scripts/export_portable_skills.py`、资源清单/来源锁、便携性测试与文档；没有修改自动点评策略，没有新增依赖、生产操作或外部投递。

## 新任务交接摘要

1. 目标：压测初评和失败分支，安装两份主对话Skill并准备后续部署。
2. 已修改文件：见“回归与改动”；来源、MIT许可与schema参考均随种子保留。
3. 已验证结果：192次并发确认无非预期异常；4次注入模型失败正确收尾；真实主对话两次通过；Linux离线种子验证通过。
4. 未解决问题：高并发目录扫描延迟、heartbeat异常收尾；Reactive Resume目标JSON导入未验证。
5. 关键命令：`python tmp/resume-skills-stress/stress.py`、`python tmp/resume-skills-stress/live_chat.py`、`pytest tests -q`、`npm test`、`npm run build`、`python scripts/verify_skill_portability.py`。
6. 下一步：优先修Worker心跳异常，再做幂等前置/目录缓存优化；投产前按当前head迁移并检查持久卷中同名用户编辑版本。本轮不投产。
