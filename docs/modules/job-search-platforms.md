# 多平台职位搜索

更新日期：2026-11-15。范围：PC 端职位搜索工作区的 BOSS 直聘原流程与猎聘官方只读搜索；前程无忧暂不接入。

## 用户流程

1. 在独立职位库路由切换 BOSS 直聘或猎聘。前程无忧入口显示“授权后开放”，当前不可选择。招呼、对话选岗和目标岗位选择流程继续只用 BOSS，不展示跨平台切换。
2. BOSS 使用现有浏览器助手；猎聘首次使用时打开官方授权页，再打开扩展设置页保存官方令牌。
3. 输入岗位名称并选择城市。BOSS 使用原城市编码；猎聘请求传城市名称，页码由 UI 的 1 起转换成接口的 0 起。
4. 搜索结果使用共用岗位卡片和详情视图。猎聘完整 JD 只从官方搜索响应读取；响应没有正文时提示打开猎聘原岗位。
5. 猎聘结果不能勾选进 BOSS 招呼清单。消息发送和简历投递目前不对猎聘开放。

## 调用链与文件

| 层 | 文件 | 职责 |
| --- | --- | --- |
| PC 工作区 | `web-ui/src/components/JobSearchWorkspace.vue` | 平台切换、连接状态、授权引导、共用搜索/详情 UI、多选能力门禁 |
| Web 桥接 | `web-ui/src/job-library-bridge.js` | 传递平台、搜索条件及令牌是否已配置查询；打开扩展设置页 |
| Content Script | `browser-extension/job-library/content-script.js` | 校验页面允许的扩展动作并转发消息 |
| 扩展后台 | `browser-extension/job-library/service-worker.js` | 从本地扩展存储读取令牌、调用猎聘官方搜索、归一响应 |
| 数据归一 | `browser-extension/job-library/liepin-data.js` | 构造搜索参数、容错读取返回结构、将岗位映射到共用字段 |
| 扩展设置 | `browser-extension/job-library/options.html`、`options.js` | 在扩展页面保存或移除令牌，令牌不经过职位库网页桥接 |
| 打包 | `scripts/package_boss_extension.py` | 将官方搜索模块与扩展设置页加入版本 ZIP |

请求链路：`JobSearchWorkspace.vue → job-library-bridge.js → content-script.js → service-worker.js → https://open-agent.liepin.com/mcp/search-job`。后台请求使用 `x-user-token`，`credentials: omit`，并仅授予该官方主机权限。令牌保存在扩展 `chrome.storage.local`；WebUI 通过 `liepin_state` 只拿到布尔状态。

## 技术取舍

- 复用现有 BOSS 搜索工作区与岗位字段，不另建第二套职位库页面；平台身份保留在每个猎聘岗位 ID 中，使用 `liepin:` 前缀。
- 使用猎聘官方只读搜索，不复制第三方适配器源码。前程无忧继续关闭，避免把未知网页/第三方抓取误报为可靠官方数据。
- BOSS 城市选择器提供城市名称；猎聘请求不复用 BOSS 九位城市码。
- 猎聘无独立详情接口时不尝试调用 BOSS `get_job_detail`。有正文则可读，无正文明确标记信息不完整。
- 令牌不通过 `window.postMessage` 桥接，以免把凭证暴露给职位库页面脚本；用户在扩展自有设置页管理。
- 扩展能力列表包含 `liepin_job_search`，但不据此推断已具备猎聘消息发送、投递或账号可用状态。
- WebUI 通过扩展 ping 能力列表确认当前助手包含 `liepin_job_search`；旧版助手不能执行猎聘搜索，页面引导升级，避免旧版将未知平台请求按 BOSS 搜索处理。

## 依赖与发行

- Chrome/Edge MV3 扩展及 `chrome.storage.local`、`chrome.runtime.openOptionsPage()`。
- 猎聘官方授权页与 `open-agent.liepin.com` 搜索服务；具体令牌有效期、可用范围及返回结构由官方服务决定。
- 扩展版本为 `0.7.0`。网页下载版本、安装教程和 ZIP 文件名必须与 `manifest.json` 同步。

## 状态与验证边界

- 已完成本地 UI、后台请求适配和扩展设置页改动，并将新文件加入发行清单。
- 本地未使用真实猎聘账号验证令牌、城市过滤、返回结构或完整 JD；当前不能承诺所有搜索结果均含完整职位描述。
- 猎聘招呼、消息、投递及前程无忧搜索尚未实现；不得显示为已连接或发送成功。
- 当前只做源码与差异人工核对，没有运行测试或生产部署。
