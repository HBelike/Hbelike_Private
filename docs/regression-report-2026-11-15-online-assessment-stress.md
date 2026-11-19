# 线上笔试助手本地压测报告

- 测试日期：2026-11-15
- 测试范围：题面归一化、流式解题协议、本地 Piston 多语言执行与函数题 Harness
- 测试环境：Windows、本机 Intel Core i5-14600KF（14 核 / 20 逻辑处理器）、31.8 GiB 内存
- 生产影响：无；未连接生产环境，未写入业务数据库，未调用付费模型
- 原始数据：`tmp/regression-2026-11-15-online-assessment/stress-results.json`

## 验收结论

本轮本地压测通过。在覆盖范围内没有发现功能错误、执行器崩溃、OOM 或容器重启。

本地 Piston 执行器在并发 8 左右达到当前机器的实用吞吐上限：Python 短任务吞吐约 83.6 次/秒；提升到并发 32 后吞吐没有继续增长，但 p95 延迟由 125 ms 上升到 395 ms。因此，当前本地部署建议将代码执行并发控制在 8 左右，并在入口增加队列或背压；并发继续提高不会明显增加吞吐，只会增加等待时间。

该结论不等同于完整生产容量：真实 LLM 提供商、登录鉴权、反向代理、数据库归档以及真实 Chrome 第三方题目页面未参与并发压测。

## 压测结果

### 题面归一化

使用 LeetCode、牛客、SQL、选择题等固定样例，测试通用捕获内容进入题面标准结构的纯本地处理能力。

| 并发 | 请求数 | 成功率 | 吞吐 | p50 | p95 | 最大延迟 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 200 | 100% | 14,955 次/秒 | 0.028 ms | 0.075 ms | 0.951 ms |
| 8 | 800 | 100% | 27,858 次/秒 | 0.020 ms | 0.036 ms | 0.189 ms |
| 32 | 1,600 | 100% | 24,051 次/秒 | 0.021 ms | 0.060 ms | 8.698 ms |

结论：题面归一化不是当前瓶颈。这里验证的是进入后端后的结构化处理，不包含浏览器 DOM 抓取、截图 OCR 或视觉模型延迟。

### 流式解题协议

通过 ASGI 进程内请求验证 `/solve/stream` 的并发响应和事件顺序。模型服务使用无外部调用的 Fake 实现，所有请求都校验了 `phase(generating) -> phase(correcting) -> solution -> done`。

| 并发 | 请求数 | 成功率 | 吞吐 | p50 | p95 | 最大延迟 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 100 | 100% | 506.7 次/秒 | 1.821 ms | 2.272 ms | 9.347 ms |
| 8 | 400 | 100% | 432.4 次/秒 | 12.455 ms | 41.629 ms | 89.306 ms |
| 32 | 800 | 100% | 667.9 次/秒 | 43.595 ms | 68.947 ms | 82.554 ms |

结论：应用层流式协议在 32 并发下无乱序和错误。由于未调用真实模型，这组数据只代表 Web 路由与协议开销，不能代表模型生成容量。

### 本地 Piston Python 并发

| 并发 | 请求数 | 成功率 | 吞吐 | p50 | p95 | 最大延迟 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 12 | 100% | 37.0 次/秒 | 21.209 ms | 87.414 ms | 87.414 ms |
| 8 | 40 | 100% | 83.6 次/秒 | 84.935 ms | 125.415 ms | 138.447 ms |
| 32 | 64 | 100% | 83.2 次/秒 | 322.784 ms | 395.323 ms | 409.907 ms |

结论：吞吐从并发 8 开始封顶；并发 32 没有失败，但排队延迟显著增加。压测结束后容器状态为运行中，`OOMKilled=false`、`ExitCode=0`，空闲内存占用约 202 MiB。

### 多语言真实执行

| 场景 | Python | JavaScript | Java | C++ |
| --- | ---: | ---: | ---: | ---: |
| 标准输入题 | 55.6 ms | 36.7 ms | 415.8 ms | 274.3 ms |
| 函数题 Harness | 73.1 ms | 48.2 ms | 401.9 ms | 1,066.7 ms |

8 个真实执行用例全部通过。C++ 函数题的编译与 Harness 开销最高，首次反馈可能超过 1 秒，属于当前实现的已测性能边界。

## 回归结果

| 检查项 | 结果 |
| --- | --- |
| 线上笔试助手后端定向测试 | 36 passed |
| 压测后再次执行后端定向测试 | 36 passed |
| 浏览器扩展测试 | 通过 |
| 笔试助手前端定向测试 | 8 passed |
| 前端生产构建 | 通过，4149 modules，8.77 秒 |
| 扩展 `service-worker.js`、`content-script.js`、`assessment-capture.js` 语法检查 | 通过 |
| 前端全量测试 | 371 passed，1 failed（与笔试助手无关的既有失败） |

前端全量测试的唯一失败为 `web-ui/src/job-search-extension-gate.test.js`：测试仍断言旧的“查看安装说明”模板片段，与当前 `JobSearchWorkspace.vue` 不一致。它不是本轮线上笔试助手压测产生的回归。

构建同时报告主包约 830.11 kB（gzip 274.66 kB），超过 Vite 500 kB 提示阈值。这是全站包体告警，不能据此判定笔试助手运行性能异常。

## 覆盖边界

以下项目本轮未压测，因此上线前不能从本报告推断其容量：

- 真实 LLM 提供商并发、限流、首 Token 时间和费用；
- 截图 OCR / 视觉模型识别及浏览器真实 DOM 捕获；
- 带登录态的 HTTP 服务、Nginx / 网关和跨网络延迟；
- 面经库保存、数据库并发和历史记录查询；
- LeetCode 之外各平台页面改版、Shadow DOM、Canvas 题面及跨域 iframe；
- 长代码、超时程序、恶意程序和超大输出等执行器极限场景。

## 建议

1. 本地与单机部署先将代码执行器并发设为 8，并配置有界队列；队列满时返回明确的“排队中/系统繁忙”。
2. 上线前用专用测试模型账号做一次小规模真实链路压测，分别记录识别、首 Token、完整答案和自动测试耗时，避免把供应商限流误判为应用故障。
3. 建立真实浏览器样本集，至少覆盖 LeetCode、牛客、CodeTop/企业自建页、SQL、选择题和 Canvas/OCR 兜底场景。

## 关键命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_online_assessment_contracts.py tests/test_online_assessment_services.py tests/test_online_assessment_execution.py tests/test_online_assessment_web.py tests/test_online_assessment_archive.py -q --disable-warnings --tb=short

Set-Location browser-extension/job-library
npm test
node --check service-worker.js
node --check content-script.js
node --check assessment-capture.js

Set-Location web-ui
node --test src/online-assessment-bridge.test.js src/online-assessment-state.test.js src/online-assessment-view.test.js
npm test
npm run build

Set-Location ../..
.\.venv\Scripts\python.exe tmp/regression-2026-11-15-online-assessment/online_assessment_stress.py
```

## 新任务交接摘要

1. 目标：验证线上笔试助手题面处理、流式协议与本地代码执行的并发稳定性。
2. 已新增：本压测报告及 `tmp/regression-2026-11-15-online-assessment/` 下的探针和原始数据。
3. 已验证：后端定向测试 36 项、前端定向测试 8 项、扩展测试与前端构建均通过。
4. 已验证：题面归一化与流式协议在 32 并发下均为 0 错误。
5. 已验证：Piston Python 在并发 8 时约 83.6 次/秒，并发 32 吞吐不再增长。
6. 已验证：Python、JavaScript、Java、C++ 的标准输入题和函数题 Harness 全部通过。
7. 未解决：真实模型、真实浏览器/OCR、鉴权网关及面经库并发尚未压测。
8. 已知无关问题：前端全量测试有 1 项职位搜索旧断言失败，主包存在 Vite 体积告警。
9. 关键命令：见本报告“关键命令”章节。
10. 下一步：上线前补真实模型小流量压测，并把单机执行并发限制在 8 左右。
