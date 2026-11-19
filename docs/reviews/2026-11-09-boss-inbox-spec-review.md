# BOSS 收件发布前需求审查

审查日期：2026-11-09。基点为 `3215973`，范围为本轮 BOSS 收件新增模块及接入位置。只读审查及内存复现不访问生产、不读取真实私聊、不触发发送。

## 已确认的可见性竞态

严重度：P2。依据 `docs/designs/2026-11-05-boss-chat-bridge-design.md` 第 55、69 行：页面切到后台后不启动新的源平台动作，回到前台重新尝试补齐。

旧版 `external-chat-client.js` 的 `refresh.active()` 只判断账号和暂停版本。复现顺序为：刷新在 `getBossChatState()` 挂起；调用 `setVisible(false)`；释放身份响应。结果为后台新增一次 `listBossConversations()` 请求，页面状态仍为 `ready`。

审查过程中，另一任务开始改写同一文件，加入源分页、活动执行资格和持续发送。2026-11-09 14:17:43 的共享工作区版本已在 `active()` 中检查 `visible`，但仍存在隐藏后重新显示使旧操作恢复的问题。当前定位：

| 位置 | 问题 |
| --- | --- |
| `web-ui/src/external-chat-client.js:249` | `refresh.active()` 只判断当前布尔可见性；隐藏、显示之后，隐藏前的请求重新满足条件 |
| `web-ui/src/external-chat-client.js:400` | `setVisible()` 不使旧 `refreshPending` 失效，前台恢复复用旧请求，未重新检查身份 |
| `web-ui/src/external-chat-client.js:319` | 旧刷新 `finally` 可清除新刷新中的 `syncing`；catch 仅判断账号，旧超时或执行资格错误可覆盖新状态 |
| `web-ui/src/external-chat-client.js:166` | 正文分页也只判断当前布尔可见性，隐藏后再显示可能继续旧分页链 |
| `web-ui/src/external-chat-client.js:137` | `checkExecution()` 响应后直接更新执行资格，尚未丢弃隐藏前请求的迟到结果 |

新版真实 controller 的内存复现输出：

```json
{"afterForegroundStateCalls":1,"expectedNewStateRequest":2}
{"completedStateCalls":1,"listCalls":1,"connection":"ready"}
```

这表示回前台没有发起新身份检查，而是等候旧身份响应，随后用旧刷新继续读取源列表。

## 提供给当前实现任务的最小修正

1. 在 controller 中增加独立 `visibilityRevision`。仅在可见性实际改变时递增；隐藏时清除旧 `refreshPending`，回前台启动新刷新。不要取消整个 controller 的 `lifecycle`，平台历史仍应允许正常查询。
2. 刷新和正文源读取开始时捕获可见性版本，后续每一步都要求版本一致。不能只增加 `visible` 布尔检查，因为隐藏后再显示会重新变为 `true`。
3. 刷新增加独立执行序号，旧 catch/finally 不更新新请求状态。历史查询保持原来的账号/会话版本约束，不受源读取版本失效牵连。
4. `checkExecution()` 的迟到响应也须经过账号、可见性版本及执行资格检查后，才能更新 `execution`；非活动结果应直接丢弃。
5. 保留现有同账号平台限制的语义：已实际返回的验证码、限流等账号级信号不能因为切会话而消失；旧网络超时、旧执行权失效等局部错误不能把新刷新变为离线或仅历史状态。具体错误归属应与执行层的共享暂停契约一致。

需要补充的回归：隐藏后释放旧身份响应不发起列表；隐藏后立即显示会发起新身份请求；旧刷新晚完成或失败不改变新刷新状态；正文分页隐藏后不继续旧链；隐藏期间仍可查询平台已保存历史。PC 场景应走真实可见性事件，不以固定等待代替状态断言。

## 修复与验证结果

初次遵照根任务协调要求只读记录。随后根确认共享文件在 14:17:43 版本上已稳定，授权以最小 `apply_patch` 修复。现已加入可见性版本和刷新执行序号：隐藏使旧读取链失效，回前台重新探测，不复用旧在途任务；旧普通错误、旧执行资格响应及旧 finally 不污染新状态。平台历史查询仍可进行，已实际返回的账号限流仍触发暂停。未改变新增发送流程或活动执行权接口。

新增独占测试，不改另一任务的既有测试：

- `web-ui/src/boss-inbox-visibility.test.js`：7 项真实 controller 回归通过；覆盖身份探测、正文分页、旧超时、旧账号响应、执行资格、历史保留和限流语义。修复前先运行，已复现缺失新探测的失败。
- `web-ui/e2e/boss-inbox-visibility.spec.js`：1 项 PC Chromium 通过。测试在浏览器页面中模拟 `document.hidden/visibilityState`，触发组件实际注册的 `visibilitychange` 处理器；不是实测操作系统标签页调度，不连接真实 BOSS。

```powershell
# 在 web-ui 目录执行
node --test src/boss-inbox-visibility.test.js
npx playwright test e2e/boss-inbox-visibility.spec.js --project=chromium --reporter=line
```

可见性竞态已在上述范围闭合；源码整体仍含并发任务的新功能，发布前须在最终冻结版本运行整体验证。

## 发布边界

首次审查的源分页、统一账号暂停与单活动执行端缺口已记录在模块文档。并发任务正在实现这些补充能力，本次旧版结论不能直接用来判断新版完成度；应待代码稳定后重新验证。真实 BOSS 求职者账号的 UID、近期 HR 消息及新回复接收仍未验收，模拟测试不能替代该发布门槛。
