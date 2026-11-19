# 本地项目链路审计与压测报告

审计日期：2026-11-04（Asia/Shanghai）。唯一基准为本地当前工作区（含未提交变更），Git HEAD `3215973`；没有以生产或历史测试记录替代本轮证据。仅新增审计报告、临时探针和日志；没有修复业务代码，没有部署。

## 结论

项目的正常路径测试基础较完整，但异常收尾、跨模块接口契约和真实运行验收存在明显缺口。优先修 Worker、实时面试断连和笔试执行判定，再优化页面体积。当前不能认定完整业务链路已经验收通过。

本轮已完成源码链路审查、后端全量测试、Web/扩展/桌面测试和构建、Mock 页面交互验证、实际 App 匿名冒烟、真实 PostgreSQL 仓储探针、真实 OCR/文档转换、短时 HTTP 与数据库并发测试，以及故障注入。授权后补充的小额外部接口验证见末节。

## 验证结果与边界

| 验证对象 | 本轮结果 | 实际说明 |
|---|---|---|
| Python `pytest tests` | 634 passed / 1 failed，20.29 秒 | 失败为调度时序敏感断言；独立复跑10/10通过，未判为业务并发故障 |
| WebUI | 237/237；production build通过 | 部分为源码/契约测试，不能替代浏览器功能验证 |
| 浏览器扩展 | 44/44 | 未在真实BOSS、牛客等登录网页执行采集/发送 |
| Windows桌面 | 8/8，typecheck/build通过 | 无真实设备、系统音频、权限弹窗或Electron全链路验证 |
| Playwright响应式 | 7/7 | 独立预览页，API与扩展均mock，覆盖布局交互 |
| 实际App PC冒烟 | 9路由HTML 200；无pageerror/5xx | 未登录业务页转/login，auth/me 401为预期；业务页功能未进入 |
| PostgreSQL | healthy；current=head `20260901_33` | 迁移一致不等于历史状态数据完全有效 |
| 真实数据库队列 | 顺序领取、2槽上限、释放复用、同会话准入通过 | 3个临时会话，单事务回滚；这是仓储SQL探针，不是完整Worker多进程压测 |
| Docling真实OCR | success，提取63字符 | 合成扫描PDF，未发送用户简历 |
| Gotenberg真实转换 | HTTP200，生成13237字节PDF | 合成HTML转换，不等同所有旧版Office格式均已联调 |
| 脚本级补验 | 12项中8通过、4失败 | 失败源于验收脚本与当前接口不一致，详见下文 |
| 流水线锁/Skill搜索性能探针 | 均通过 | 临时数据/Mock，无微信、生成媒体或真实搜索吞吐证明 |

证据目录：`tmp/audit-20261104/`。真实App截图为`app-*.png`；响应式测试不得与实际App截图混为一谈。

## 已复现与源码确认的问题

P1为优先阻断修复；P2为正常开发前应补齐的正确性问题；P3为性能或体验优化。源码确认与现场复现分别标记。

### P1：Worker心跳异常跳过任务收尾，并传播到主循环

- 位置：`src/career_assistant/turn_worker.py:344`、`:370`、`:257`。
- 仓储heartbeat抛异常后，finally中await heartbeat_task再次抛出，后面的finish_claim不执行；gather将异常传播到Worker主循环。
- 故障注入已复现：ConnectionError，finish_claim调用0次。普通成功路径测试无法覆盖此故障。
- 建议：明确续租失败和租约丢失后的停止/收尾语义，单Turn失败受控落盘，避免直接终止整个调度循环。补heartbeat异常、finish异常测试。
- 证据：`worker-heartbeat-fault.py`。

### P1：过期任务仅启动时回收，存在重启后永久卡住的窗口

- 位置：`src/career_assistant/turn_worker.py:243`、`src/career_assistant/persistence/turn_job_repository.py:647`。
- recover_expired在循环外仅调用一次；重启时尚未过期的旧running任务，之后到期也不会被该Worker主动回收，claim只领取queued。
- 证据等级：源码控制流确认；尚未执行真实杀进程并等待租约到期的完整复现。
- 建议：复用现有租约机制周期回收，补“到期前重启，启动后才到期”测试。无需增加新基础设施。

### P1：实时面试满队列断连时，关闭流程阻塞

- 位置：`src/career_assistant/live_interview/session_manager.py:343`、`:352`、`:359`；`live_interview/web.py:332`、`:346`。
- Web先取消sender，manager.close取消回答后又向满的1000条队列put终止事件；此时无消费者，后续repository.end和connections.discard不能完成。
- 已复现：等待中的Fake回答、填满队列，wait_for(close,0.2)超时。这是内存状态机故障注入，无真实语音服务。
- 建议：断连关闭不再写入无人消费的队列，或采用非阻塞终止投递；会话终态和连接释放独立收口。

### P1：笔试执行异常可能被标为测试通过

- 位置：`src/career_assistant/online_assessment/execution.py:170`、`:193`。
- HTTP200但缺run时使用空字典；缺失或None退出码被转为0，未充分处理signal。
- 已复现：`{}`与`run={stdout:'',code:None,signal:'SIGSEGV'}`在预期输出为空时均被判passed。
- 建议：要求完整合法执行结果与明确退出码；任何崩溃signal不得通过，缺run应标执行失败。补恶意/截断/版本不兼容响应测试。

### P2：Java Harness会使正确答案编译失败

- 位置：`src/career_assistant/online_assessment/execution.py:370`、`:387`、`:392`。
- 完整用户代码被拼在Main类后，import出现在类定义后面；数组按运行时值粗略推断，double[]参数生成int[]字面量。
- 真实javac已复现两类编译失败：合法import的位置错误，以及`new int[]{1.5,2.5}`类型错误。
- 建议：提取imports到文件顶部，依据函数签名生成类型；至少覆盖空数组、double/long/boolean数组和嵌套数组。超出支持范围应明确提示。
- 现有多语言测试Mock执行器返回成功，只检查源码片段，未编译。C++类似类型边界为待验证，尚未实编译，不计为已复现缺陷。

### P2：周榜“周增长”受周内快照刷新影响

- 位置：`src/tasks/search_task.py:53`、`src/repositories/star_snapshot_repository.py:92`、`src/services/weekly_ranking_service.py:100`。
- 基线选择当前日前最近一次快照，没有固定周起点；管理员周内刷新后改变了统计间隔。
- 确定性复现：同周当前stars=1200，上周五基线1000时增长200；周四生成1190快照后增长变10。
- 建议：按周起点选择基线，记录实际采样区间和缺失状态；不要把不同采样间隔的差值直接作为同口径周增长排名。

### P2：桌面采集失败后系统音轨未释放

- 位置：`desktop-interview-assistant/src/audio/capture.ts:57`、`:63`。
- 系统音频获得授权后，麦克风获取在清理try范围外失败，系统音轨不会stop。
- 对真实TS函数做接口Stub复现，systemAudioStopCalls=0；未执行真实设备授权流程。
- 建议：让所有获取到的资源都进入同一个失败清理边界，覆盖麦克风拒绝和设备不存在。
- 证据：`repro-desktop-audio-leak.cjs`及`.log`。

### P2：桌面独立启动默认端口已过时

- 位置：`desktop-interview-assistant/electron/main.ts:10`、`src/pages/SetupPage.vue:19`。
- 默认8000，与标准本地后端18080不一致；直接启动首次预检连错服务，Web launcher传参或手动修改可绕过。
- 建议：统一默认配置来源，覆盖“独立启动”和“网页唤起”两条路径。

### P2：数据库存在旧版无租约running任务

- 只读确认1条历史running，创建时间2026-08-07T12:02:48Z，lease_owner/lease_expires_at均NULL，attempt_count=0。
- `turn_job_repository.py:648`的过期条件不匹配NULL，`:136`的准入检查却将其视为active，因此对应会话可能无法继续。
- 本轮未修改此历史记录，也未读取消息正文。应先确认业务来源，再做受控一次性状态兼容处理。

### P2：关键验收脚本失效，pytest全绿不足以证明链路完整

| 脚本 | 实测失败原因 |
|---|---|
| `verify_career_api.py:76` | intake现有响应不含脚本期待的message，KeyError |
| `verify_career_sse_heartbeat.py:44` | 把async_generator当同步迭代器，TypeError |
| `verify_interview_library.py:90` | list_tree已改关键字参数，脚本仍位置传参 |
| `verify_interview_library_api.py:106` | 更新请求缺company_name/role_name，422 |

建议将这些验收入口纳入持续执行，修正其契约；补一条真实数据库+本地可控Provider的HTTP→Turn→Worker→SSE→落库测试。不能简单删除失败断言或一律Mock成成功。

### P3：主包体积、首字延迟与时序测试待优化

- `web-ui/src/App.vue:3`起静态导入多个大页面，主chunk为695.57kB，gzip229.18kB；建议按路由懒加载。尚无真实首屏耗时，不能把体积直接视为卡顿复现。
- `turn_worker.py:97`按512字符聚合delta，缺时间阈值；短回答可能在完成时才显示。建议增加时间阈值，并测首个可见正文耗时，当前为源码性能风险。
- 100用户模拟用例依赖20ms任务重叠，断言峰值必须等于8；首轮7不代表并发失效，10次独立复跑均通过。建议Barrier固定重叠窗口，再验证上限与无同会话重叠。

## 分模块待完善清单

| 模块 | 已获得证据 | 下一步应补的验收 |
|---|---|---|
| 登录/管理台 | 匿名鉴权、首管理员脚本通过 | 实际登录后的权限和功能开关闭环；本轮未通过真实浏览器登录 |
| 求职对话/简历/JD | 仓储、Intake、解析降级、OCR通过；Worker故障已复现 | HTTP提交→Worker模型回复→SSE重连→刷新历史完整链；真实简历不用于测试 |
| 多模型连接 | 客户端Mock回归通过 | 各Provider真实最小请求及能力差异；不能靠固定模型名称长期保证可用 |
| 职位/BOSS | 扩展44测试通过 | 真实DOM采集、分页/登录失效/岗位变更；真实发送未执行 |
| 面经/RAG | 权限回归有覆盖；2个旧脚本失效 | 同维度更换embedding模型：查询未过滤embedding_model（retrieval.py:174、repository.py:583），需索引版本过滤与重建；未实测召回下降 |
| 实时面试 | 后端回归、协议测试；满队列缺陷复现 | 真音频→ASR→末句触发→回答→归档；无界deque在慢模型下的积压测试 |
| 桌面面试 | build/typecheck/8测试通过 | 修音轨释放、默认端口；补真实设备与权限拒绝测试 |
| 线上笔试 | 正常路径Mock测试通过；执行器/Harness缺陷复现 | 实际Piston及各语言编译；第三方平台采集；避免把Harness故障归为算法错误 |
| 周榜/文章/图/音视频/微信 | 锁、配置与部分Provider契约覆盖 | 修周增长口径；合成仓库输入贯穿文章、图稿、布局；真实草稿与发布未执行 |
| 技能库/评测/日志 | Skill搜索性能探针、部分单元测试 | 实际Skill调用到Tool返回、日志订阅恢复、评测数据链路；未做每个管理操作实测 |

面经跨组织可读是现有测试明确的公开库设计，写入按归属控制，本轮不将其误报为权限漏洞。架构旧文档所述“同会话可连续排队”与当前拒绝第二个活动Turn也不一致，应按当前产品决定更新文档。

## 短时压测

脚本：`tmp/audit-20261104/local_load.py`；原始结果：`load-results.json`。直连本机API，关闭HTTP环境代理；同一机器运行数据库和其他服务，未做独立负载机、持续耐久、资源采样或大数据量测试。

| 场景 | 请求数/每档 | 并发1 P95 | 并发8 P95 | 并发32 P95 | 并发32吞吐 |
|---|---:|---:|---:|---:|---:|
| API health | 128 | 5.51ms | 29.33ms | 79.57ms | 541.60/s |
| bootstrap-status（数据库读取） | 128 | 6.59ms | 25.54ms | 114.06ms | 436.10/s |
| 匿名导航鉴权 | 128 | 7.05ms | 65.01ms | 196.97ms | 398.63/s |
| 会话创建/读取/删除 | 96 | 15.73ms | 35.52ms | 205.01ms | 410.65周期/s |

共1152个HTTP请求与288个会话生命周期；HTTP预期状态全部符合，数据库最终有效测试0错误。匿名导航384次401为预期拒绝，不是业务请求成功，也不能当成登录后导航吞吐。数据库每个生命周期执行finally删除，仅使用合成测试标题。开发探针首轮存在调用签名错误，修正探针后才得到本表，未归为业务错误。

32并发时数据库周期吞吐低于8并发（520.05→410.65/s），尾延迟增大；这只说明此本地短测下排队/争用开始显现，不能单凭结果认定数据库瓶颈或推算可承载用户数。需要登录后真实业务混合负载、连接池等待与慢SQL证据，才能决定调并发还是改查询。

`/api/health`仅返回固定运行信息（`src/web/api.py:268`），不检查PostgreSQL与Worker；此前数据库不可用时也曾返回ok。建议区分存活与业务就绪，至少让验收同时检查数据库/Worker；无需扩展成新监控平台。

## 建议实施顺序与通过标准

1. **先修正确性与收尾**：Worker心跳/租约、面试关闭、执行器异常判定。以故障注入不挂死、单任务受控终止、异常不会显示通过为标准。
2. **再修用户可见错误**：Java Harness、桌面资源释放与端口、周榜基线；用真实编译、资源计数和固定周窗口验证。
3. **恢复可执行验收链**：更新4个旧脚本，增加HTTP→真实DB→受控Provider→Worker→SSE→历史恢复，覆盖失败、断开和重试。正常回归与故障测试应共同通过。
4. **最后按数据优化**：路由懒加载、流式首字延迟、RAG索引版本、慢模型积压；先测指标再调参数，不增加与当前需求无关的基础设施。

## grilling审视：需要明确的产品取舍

1. **同会话到底允许连续排队，还是只允许一轮活动任务？** 当前仓储拒绝第二轮，旧架构承诺连续排队。建议先明确当前交互规则，并使按钮、错误提示、文档与真实准入一致，不能以旧文档推断代码应有的功能。
2. **笔试“通过”究竟证明什么？** 当前只可能证明所选语言Harness下的可见测试，不能证明隐藏测试或第三方平台通过。应先修执行结果真实性，再决定增加题型和平台数量。
3. **实时面试积压时保什么？** 无界队列保留全部提问会使答案越来越滞后。建议明确显示积压并提供取消/跳过；是否合并旧提问应由产品决策，不静默丢弃。
4. **周榜强调固定周窗口还是最近快照增量？** 两者会得到不同排名。建议固定周窗口并展示缺失基线；若继续最近增量，应同步改变指标名称与排序说明。
5. **何时可以说“完整验收通过”？** 建议要求真实登录、合成资料、真实DB、受控模型、SSE断连恢复与历史重读形成一条可复跑链。单独的Provider连通、Mock页面或健康接口不能替代这条链。

这些属于后续实现的待定决策，本轮仅提供建议，没有擅自改动产品行为。

## 复跑命令

```powershell
# 项目根目录
.\.venv\Scripts\python.exe -m pytest tests -q --disable-warnings --tb=short
.\.venv\Scripts\python.exe tmp/audit-20261104/local_load.py
.\.venv\Scripts\python.exe tmp/audit-20261104/reproduce_module_findings.py
.\.venv\Scripts\python.exe tmp/audit-20261104/worker-heartbeat-fault.py
.\.venv\Scripts\python.exe scripts/verify_career_docling_live_service.py
# 下列命令应在对应目录执行
# web-ui: npm test; npm run build; npm run test:e2e:responsive
# browser-extension/job-library: npm test
# desktop-interview-assistant: npm test; npm run typecheck; npm run build
```

故障复现脚本退出0代表确认当前缺陷，不代表业务通过。数据库探针必须先确认本地地址，队列探针不得领取用户已有queued任务。

## 小额真实外部调用补验

用户已明确授权模型/语音/采集API小额真实验证；只使用合成内容及公开页面，不执行BOSS发送、微信草稿或发布。

- **DeepSeek真实客户端通过**：复用项目OpenAICompatibleChatClient及现有档案，请求deepseek-v4-flash，合成2+2返回4；1次调用，0.773秒，实际input14/output1 Token；上游reported model为deepseek-flash。证据`live-model.json`。
- **豆包TTS真实失败**：2次短句合成均抛DoubaoTtsApiError，第二次确认HTTP403、上游业务码45000030。这是当前本地实际依赖阻断，先排查当前配置与上游资源权限/接口契约，不能仅凭错误码断言具体原因。没有生成可用音频，不能将媒体链路标为通过。
- **Firecrawl真实采集通过**：公开example.com共2次scrape，首轮1.692秒、959字符；第二次正文核查通过。onlyMainContent移除标题，首轮“正文缺精确标题”是探针误报，未归为业务故障。
- **DashScope实时ASR转写通过**：1次会话，qwen-audio-3.0-asr-flash-streaming，本机System.Speech合成203696字节PCM；18.088秒内final准确识别合成英文句子。close超过探针5秒阈值后被取消，只是关闭延迟观察，未证明超过库默认超时，不能归为识别失败或已确认业务bug。
- **调用总量与证据**：模型1次、Firecrawl2次、TTS2次、ASR1会话；安全汇总`external-live-final.json`、模型`live-model.json`。这些是Provider连通与最小功能验证，不能替代完整ASR→回答→归档或浏览器登录后采集链路。

本地运行收尾：API18080、Web5173与独立Worker保持启动；压测临时会话剩余0（`cleanup-check.json`），真实队列探针整体回滚。未处理原有历史僵尸记录。

## 新任务交接摘要

1. 目标：以本地唯一基准完成项目审计，输出问题、完善项、优化顺序与证据。
2. 已修改文件：新增本报告及`tmp/audit-20261104`探针/日志，未改业务源码；前端构建更新忽略的dist。
3. 已验证结果：见结果表与压测表；首轮pytest有1个时序敏感失败，未宣称全绿。
4. 未解决问题：P1/P2缺陷尚未修；登录后全链路、真实设备与第三方业务动作未验收。
5. 关键命令：pytest、npm test/build、local_load.py、故障复现脚本。
6. 下一步：优先修Worker、实时面试清理、执行判定，再恢复验收链。
