# 岗位验证误判与结果状态修复

## 目标与证据

用户反馈岗位列表已有15条，但出现“本次没有获取到岗位”和安全验证告警，详情缺少正文仍显示“详情已更新”；用户确认当时 BOSS 官方页可以正常浏览，没有滑块或验证码。

源码确认：`service-worker.js` 原岗位读取函数在请求前，对整页文本匹配任意“安全验证”“环境存在异常”“请稍候”，导致普通说明和加载文案也能返回 verification。用户当时页面的具体 DOM 未采集，因此能确认误判机制与复现输入，不能断言已核实当时命中的确切元素。

前端另有两个独立问题：`searchError` 同时承载首次搜索、加载更多和保存失败，但标题固定写“没有获取到岗位”；详情头的“已更新”无条件展示，没有检查完整 JD。扩展 ping 成功或选中缓存详情还会清除页面的 attention 状态，无法证明招聘站问题确实解除。

## 实现与取舍

- 岗位读取移为 `boss-data.js` 的独立 `fetchBossJobData`，由 Service Worker 作为 `chrome.scripting.executeScript` 的 MAIN world 函数执行。所有规则显式作为参数传入，避免注入后的函数依赖模块闭包。
- 复用 `boss-greeting.js` 已有的 `BOSS_CHAT_PAGE_EVIDENCE`，仅验证路由、可见挑战控件和明确验证提示触发页面拦截；普通安全说明、“请稍候”和隐藏容器不拦截。接口返回非零 code 且明确要求验证时仍停止。原登录、HTTP错误、限流与单次会话刷新机制保留。
- `JobSearchWorkspace` 根据是否已有岗位决定失败标题，加载更多失败时保留原列表。完整正文存在才显示“完整详情已获取”；否则展示获取中、受阻或只有摘要，并隐藏误导性的详情更新时间。
- `bossAttention` 保存招聘站业务状态，扩展握手成功与缓存命中不再视为验证解除。成功读取新搜索结果、分页或详情后才恢复正常状态。
- 详情预取在组件内改为逐条领取，与扩展已有串行队列一致；遇到登录、验证、限流或刷新失败停止领取下一条。`shouldContinue` 同时校验搜索序号、预取序号和组件生命周期，旧搜索晚到结果不再合并，也不会继续领取旧任务。已经在途的读取不做浏览器级强制取消。
- 不新增插件协议动作，不改变岗位保存/版本契约，不修改招呼语发送链路。此修复不包含上一轮压测报告中另外3项缺陷。

## 调用链与依赖

`JobSearchWorkspace → job-library-bridge → Content Script → Service Worker.runBossFetch → MAIN world.fetchBossJobData(endpoint, BOSS_CHAT_PAGE_EVIDENCE) → BOSS岗位只读接口 → classifyBossFailure/normalizeDetailResponse → 详情加载器与前端状态`。

后台依赖既有 `boss-data.js`、`boss-greeting.js` 和 Chrome scripting API；前端复用既有 loader/prefetch 与 connection 模块，无新增运行依赖。

## 版本和本地验证

扩展与网页下载入口升级为 **0.6.1**，安装教程同步版本；本地生成 `web-ui/public/downloads/find-job-boss-helper-v0.6.1.zip`。发行测试逐文件核对ZIP与源码字节，并检查运行时相对import均在包内。

2026-11-09 本轮执行结果：

| 命令/范围 | 结果 | 证据边界 |
| --- | --- | --- |
| 扩展 `npm test` | 99通过 | Node，含5项新增MAIN world序列化函数测试，无真实BOSS访问 |
| 前端 `npm test` | 370通过 | Node，含预取搜索切换/组件退出后停止测试 |
| Chromium：job-search-card、chat-job-search-flow、job-search-verification | 16通过 | 真实浏览器，API与扩展响应为合成fixture，PC优先；包含既有手机冒烟 |
| `pytest tests/test_boss_extension_distribution.py -q` | 3通过 | 真实ZIP源码一致性、版本链接、依赖完整性 |
| `npm run build` | 通过 | 保留既有chunk大于500kB提示，未修改构建策略 |

定向浏览器场景验证：详情受阻仍展示15条；加载更多失败不出现空结果标题；预取只发出1个失败请求；重新握手或查看已缓存JD不假装解除验证；用户主动重试成功后完整JD可见、目标岗位可确认，分页重试清除对应告警。新增浏览器测试首次因fixture缺少规范化结果中的skills/welfare数组导致渲染失败，补齐测试数据后通过，没有据此改动业务契约。

日志：`tmp/job-search-verification-fix/{extension,frontend,browser,distribution,build}.log`。

## 生效与边界

此修复只在本地完成，未连接或部署生产，未访问用户招聘账号、执行真实招聘请求或发送招呼。真实BOSS账号恢复需要在用户浏览器重新加载包含修复的扩展，再刷新网页并主动重试；单独更新网页无法替换浏览器内已经加载的Service Worker。若加载的是旧ZIP解压目录，需要先更新该目录中的扩展文件；不要求删除扩展再添加。

真实验证和接口限制仍按官方页面处理；本次没有绕过验证，也没有真实账号端到端验收。
