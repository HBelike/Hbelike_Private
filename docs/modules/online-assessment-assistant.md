# 线上笔试助手

## 设计目标

线上笔试助手是求职助手中的独立 PC 页面。用户在 Chrome/Edge 的题目标签页主动点击“求职浏览器助手”后，系统读取当前视口，自动完成题面分析、答案生成、公开样例与 AI 边界测试，并在失败时最多自动修复两轮。系统只提供参考，不写入或提交第三方页面。

整场笔试可由用户确认后保存为一份面经；每道题为一个章节。未归档会话只保存在助手窗口的 `sessionStorage`，原始截图和页面全文不会写入面经库。

## 技术取舍

- 复用现有 Chrome MV3 扩展，通过 `activeTab` 获得一次性页面权限，不增加 `<all_urls>`。
- DOM 提取是主识别路径；低置信度、Canvas/iframe 或字段冲突时才复用平台 `CloudVisionRouter`（默认 Qwen 3.6 Flash）复核当前视口，失败后安全回到人工确认。
- 解题、测试生成与修复复用现有 `ModelGateway`、模型档案和 OpenAI-compatible 客户端，不在扩展保存 API Key。
- 模型代码只发送到独立 Piston 容器。FastAPI、PostgreSQL 和 WebUI 不直接执行代码。
- 函数题由 Python、JavaScript、Java、C++ Harness 连接 JSON 参数；标准输入输出题直接传入 stdin。
- 作答路线覆盖单文件算法题、SQL、选择题和简答题；只有算法题进入 Piston，SQL 明确标注未连接数据库执行。前端工程、多文件、交互题和第三方隐藏测试仍不执行。
- Adapter Registry 深度适配 LeetCode、HackerRank、牛客；CodeSignal、Codility、赛码先使用通用语义 DOM 与视口识别。编辑器采集优先读取 Monaco/CodeMirror 可见代码，再由后端补全函数签名。
- 模型答案不合约时由同一配置模型自动纠正一次，页面显示“校正答案”；网络、鉴权、额度和空返回不会被格式重试掩盖。

## 调用链

```text
题目标签页点击扩展
  → Service Worker 读取可见 DOM 与当前视口截图
  → chrome.storage.session 保存 5 分钟一次性 launch_id
  → /career/online-assessment 领取采集并删除 launch_id
  → POST /api/career/online-assessments/analyze
  → POST /api/career/online-assessments/solve/stream
  → POST /api/career/online-assessments/execute
  → Piston 编译/执行，失败最多修复两轮
  → 页面编辑、复制、重新识别或重新测试
  → 用户确认后 POST /archive 写入面经库
```

“重新识别”替换当前题目；“补充题面”合并用户滚动后的新视口。两者都只重新读取最初绑定的题目标签页和同一来源域名，跨站导航后必须重新点击扩展。`run_version` 会使旧请求失效，避免迟到结果覆盖新题目。

扩展安装、更新或浏览器重新启动时会向已经打开的业务页幂等补注入通信脚本。笔试页检测到旧版本时显示实际版本、最新版下载、安装说明和“重新检测”，更新后可以在原页继续。人工点击“确认题面并解答”时，确认后的置信度与缺失项会先写入会话状态，再开启生成任务，避免新任务恢复旧题面。

管理台使用独立的 `career_online_assessment` 子功能开关控制求职助手按钮和本页直达路由。开关关闭后对普通用户和管理员同时生效，但管理员仍可进入固定开启的管理台恢复配置。历史共享键 `career_interview_tools` 会在读取时继承为线上笔试与面试大师两个新开关。

## 本地启动

首次启动或运行时缺失时执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_online_assessment_executor.ps1
```

脚本使用 `docker-compose.online-assessment.yml` 启动固定摘要的 Piston 官方镜像，并安装固定版本的 Python、Node.js、OpenJDK 和 GCC。服务只绑定 `127.0.0.1:2000`，代码运行时禁用出站网络。重复运行会跳过已安装语言。

后端默认读取：

```text
CAREER_PISTON_BASE_URL=http://127.0.0.1:2000
```

浏览器扩展开发态可在扩展管理页加载 `browser-extension/job-library`。对外安装包为 `web-ui/public/downloads/find-job-boss-helper-v0.4.0.zip`。

## 数据与隐私边界

- 扩展不读取 Cookie、密码、登录 Token、隐藏测试或页面原始 HTML。
- 截图不写磁盘、不进数据库；题面分析完成后前端即丢弃截图字段。
- 会话保存前展示公司、岗位、日期及题目数量，必须由用户确认。
- 面经只保存确认题面、最终代码、解题思路、复杂度与测试摘要；同一来源和题面使用确定性标记去重。
- 本地测试通过只表示可见测试通过，页面不会承诺第三方隐藏测试通过。
- 是否允许外部辅助由第三方笔试规则决定，产品不提供隐身、反检测、自动填写或自动提交。

## 依赖与运行限制

- Piston 镜像：`ghcr.io/engineer-man/piston@sha256:2f66b7456189c4d713aa986d98eccd0b6ee16d26c7ec5f21b30e942756fd127a`。
- 编译 10 秒、运行 3 秒；编译内存 512 MiB、运行内存 256 MiB。
- 每次最多 20 个测试；浏览器只接收截断后的 64 KiB 输出和 4,000 字安全错误摘要。
- Java/C++ Harness 覆盖常见基础类型、字符串、数组与嵌套整数数组；复杂对象、链表、树和自定义类型需要后续增加专用序列化器。
- 当前 Piston 仅用于本地开发。生产代码执行需要单独授权并部署到与应用、数据库隔离的执行节点。

## 验证记录

### 2026-09-02 多平台识别与答案契约升级

- **识别链路**：升级为 Capture Contract V2 与声明式 Adapter Registry，深度覆盖 LeetCode、HackerRank、牛客，其他站点使用通用当前视口识别；加入来源域名绑定、字段来源、题型与选项契约。
- **作答链路**：算法题继续生成与测试；SQL、选择题、简答题生成对应参考答案但不进入 Piston。未知语言不再静默改成 Python，切换语言会清除旧答案与旧测试。
- **模型稳定性**：答案 JSON 或 Pydantic 校验失败时由同一模型自动纠正一次；仅做 `assumptions` 的无歧义归一化，连续失败给出明确错误。
- **视觉兜底**：低置信度截图通过现有 CloudVision 连接使用专用题面提示复核，截图和无效模型原文均不持久化。
- **验证范围**：扩展 fixture、函数 Harness、题型路由、视觉复用、流式校正和前端状态均有自动测试；扩展包升级为 `0.4.0`。

### 2026-09-02 Service Worker 生命周期补充修复

- **问题原因**：`onInstalled` 与 `onStartup` 使用裸 `void injectBridgeIntoOpenAppTabs()`；扩展再次重载时，后台查询标签页会拒绝且没有末端处理。题面失败反馈中的 Chrome API 也可能在 runtime 失效后同步抛错，形成第二条未捕获异常。
- **修复结果**：安装、启动、题面点击和失败反馈统一进入安全后台任务边界；Chrome API 批量反馈逐项隔离同步异常与异步拒绝，消息响应端口关闭也会安全结束。
- **验证范围**：新增后台同步抛错、异步拒绝、失败反馈二次失效和入口接线四类测试；扩展全量 41 项通过，安装包升级为 `0.3.4`。

### 2026-09-02 扩展上下文失效修复

- **问题原因**：扩展更新或重新加载后，旧页面中的 content script 监听器仍然存活，但其 Chrome runtime 已失效；`chrome.runtime.sendMessage` 会同步抛出 `Extension context invalidated`，原有 Promise `.catch()` 无法捕获。
- **修复结果**：通信桥接在调用前检查 runtime，并统一捕获同步抛错与异步拒绝；确认上下文失效后解除旧监听器且不抢先返回失败，由新版监听器接管请求。
- **验证范围**：新增同步失效、异步失效和普通连接错误三类 VM 隔离回归测试，安装包升级为 `0.3.3`。

### 2026-09-01 Chrome 隔离注入修复

- **问题原因**：`chrome.scripting.executeScript` 只序列化传入函数，旧实现仍引用模块作用域的 `detectAssessmentPlatform` 和 `extractFunctionSignature`，因此进入页面后函数不可见，题面采集没有结果。
- **错误呈现**：Service Worker 把已处理的识别失败写入 `console.error`，Chrome 因此在扩展错误页登记了一条错误。
- **修复结果**：页面采集函数改为自包含；Service Worker 区分脚本执行异常和空结果，并把失败原因放入扩展图标标题，不再制造错误记录。
- **验证范围**：新增 Chrome 函数序列化隔离测试；扩展全量 34 项通过，安装包升级为 `0.3.2`。

### 2026-09-01 LeetCode 中文站与确认状态修复

- **问题原因**：平台识别遗漏 `leetcode.cn` 根域名，导致专用题面选择器没有启用；同时人工确认只修改 Vue ref，启动下一轮时又被 `sessionStorage` 中的旧题面覆盖。
- **修复结果**：中文站根域名进入 LeetCode adapter；从 Monaco/CodeMirror starter code 提取 Python、JavaScript、Java、C++ 函数签名，后端再做同一套确定性回退；人工确认先持久化再生成。
- **扩展恢复**：旧版本页面提供下载和重新检测，Service Worker 在安装、更新和浏览器启动时重新注入 bridge。
- **验证范围**：扩展平台/签名测试、后端题面契约测试、WebUI 状态测试与 production build。

- 扩展测试：41 项通过（含页面桥接与 Service Worker 生命周期失效回归）。
- 后端全量 598 项通过；其中线上笔试与迁移相关测试覆盖题面契约、模型输出、函数/标准输入输出执行、自动修复、归档和 API。
- WebUI 测试：全量 225 项通过；Vite production build 成功。
- Piston 本机已启动，Python、JavaScript、Java、C++ 四种语言逐一执行 `42` 冒烟用例，并以 `Solution.twoSum` 函数题完成真实 Harness 编译运行，全部通过。
- PC 浏览器实测：等待态、三栏工作台、可编辑代码、公开/AI 测试区和面经归档弹窗均正常。
- 本地 PostgreSQL 已从 `20260830_31` 升级到单一 Alembic head `20260901_33`。

完整产品约束见 [设计说明](../superpowers/specs/2026-09-01-online-assessment-assistant-design.md)，实现步骤见 [实施计划](../superpowers/plans/2026-09-01-online-assessment-assistant.md)。

多平台升级见 [设计说明](../superpowers/specs/2026-09-02-online-assessment-multi-platform-design.md) 与 [实施计划](../superpowers/plans/2026-09-02-online-assessment-multi-platform.md)。
