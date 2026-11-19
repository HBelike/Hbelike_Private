# PWA 分享接收实施记录

目标：继续已确认的第二阶段，支持安装后的 Android Chrome 从系统分享菜单交付职位链接、文字或单个文件；仍使用原简历／岗位解析与人工确认流程。只在本地实施，不部署生产。

## 设计与技术取舍

采用 manifest 的 POST multipart share_target → 仅拦截该入口的 Service Worker → 浏览器临时 IndexedDB → `/career/import` 独立确认用途 → 当前登录会话内的待导入材料 → 用户选择会话 → 原资料 Sheet。接收和移交均不调用解析、模型或资料保存 API。文件仍限制单个、10MB；不增加多图、离线工作台或业务 API 缓存。

先完成 PC 接收、登录恢复、用途选择和原表单移交，再验证手机单列与短屏。页面沿用现有蓝绿主题、中文标题和资料卡，以“收到什么 → 用于简历或岗位 → 在当前会话校对”为单一任务，不堆叠完整聊天或简历工作区。

复用官方浏览器原语，无新增依赖。本项目采用 Chrome 官方的 Service Worker 读取 POST FormData、再用 303 转为 GET 的浏览器侧方案；也允许服务端接收 POST，但本轮不把未确认材料上传服务器。Android 的 URL 常在 text，不能只读 url。参考 [Chrome 官方实现说明](https://developer.chrome.com/docs/capabilities/web-apis/web-share-target)、[GoogleChrome 官方样例](https://github.com/GoogleChrome/samples/blob/gh-pages/web-share/README.md) 和 [MDN share_target](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Manifest/Reference/share_target)。GoogleChrome/samples 为 [Apache-2.0](https://github.com/GoogleChrome/samples/blob/gh-pages/LICENSE) 的浏览器演示仓库，web-share 目录最近一次提交核对为 [2026-09-18](https://github.com/GoogleChrome/samples/commit/68dc8360eec16453425620095f77efcdca59c4dc)；它不是产品级导入库，不作为运行依赖。只采用官方模式，独立编写业务接收与状态衔接。相较引入完整 PWA／离线缓存工具，窄 Service Worker 的依赖和更新成本更低，也不改变现有 API 新鲜度。

安装需 HTTPS（localhost 开发例外）、manifest 和相应图标；普通局域网 HTTP 不作为手机验收地址。iPhone 和不支持 share_target 的浏览器继续使用手动复制、截图和文件选择。见 [安装要求](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable)。

## 实施步骤

- [x] 增加 manifest、沿用现有标识的图标、最小 Service Worker；验证真实 POST／303、文字和原始文件字节、单文件限制及空材料。
- [x] 增加独立分享确认页、登录后恢复入口、取消与退出清理；链接从 url／text／title 中确定性识别，多个链接由用户选择，不自动抓取。
- [x] 接入求职助手待导入提示和资料 Sheet，明确选择会话后才移交；不覆盖未确认草稿，不自动保存或模型分析。
- [x] PC 6 项先通过，再补手机短屏、跨模块、前进返回和安装说明；原模块权限继续生效。
- [x] Node、浏览器回归、production build 的本地静态预览与模块文档；未安装操作系统分享／真机的边界另记下文。

## 状态边界

分享材料只为用户主动交付建立临时浏览器记录，完成移交或取消即删除，退出登录清理。超过 24 小时视为过期，下次读取／接收时清理；不新增云端简历附件存储。移交之后沿用原登录会话内草稿规则，刷新／关页不恢复。分享不读取其他 App 的登录态；岗位链接仍可能需要截图或正文回退。

## 文件职责与调用链

- `web-ui/public/manifest.webmanifest` 注册 POST multipart 分享目标；`public/icons/app-{192,512}.png` 由现有 favicon.svg 原样渲染，不引入新品牌或第三方图片。
- `public/share-worker.js` 只拦截同源 `/share-target` POST；检查单文件、10MB、格式和文本长度，使用随机记录 ID 进入临时 IndexedDB，再 303 至确认页。`share:read/delete/clear` 通过 MessageChannel 操作暂存，普通请求直接走原网络，不创建 CacheStorage。
- `src/material-share.js` 负责注册、消息桥、URL 候选、登录返回地址与 Sheet 草稿构造；`MaterialSharePage.vue` 只负责收到的材料、用途和来源选择，不调用解析／保存 API。分享 ID 留在认证 URL 中，登录刷新及注册／找回密码认证子页面不丢入口。
- `App.vue` 将 `/career/import` 归入原求职模块权限，账户菜单提供“手机分享导入”说明入口。当前登录会话保存一份待导入材料，退出清除；同路由 query 变化按地址重新读取，避免浏览器返回时展示其他材料。`styles.css` 为该路由单独设置资料卡滚动，顶栏留在卡片外，消除短屏正文从顶栏边缘露出的显示问题。
- `CareerAssistantPage.vue` 显示待导入提示，必须先选择或新建会话，再明确移交。已有未确认草稿先询问替换；只替换该用途，另一类资料及附件继续保留。原 Sheet 仍由用户主动解析、编辑和确认。
- `docker/nginx/default.conf` 为接收脚本和 manifest 配置更新缓存策略；无 Service Worker 时分享 POST 303 回退到手动导入说明。这里只修改并验证本地配置，未重建现有容器或连接生产。

## 验证与证据

PC 首批 6/6 通过后，才加入手机布局与交互。最终专项增加为 11 项，覆盖真实浏览器接收、文件字节、登录刷新、多个链接、取消／退出清理、权限拒绝、空／超大／不支持材料、同路由返回、手机短屏和草稿替换。无效材料不会把部分资料当作导入成功；空 multipart 的浏览器解析差异已改为明确的无内容提示。

- [PC 首批日志](../../tmp/regression-20261109/pwa-pc.log)、[专项复核日志](../../tmp/regression-20261109/pwa-final-focused.log)；[PC 预览](../../tmp/regression-20261109/pwa-share-pc.png)、[手机正文](../../tmp/regression-20261109/pwa-share-mobile.png)、[手机操作区](../../tmp/regression-20261109/pwa-share-mobile-actions.png)。手机覆盖 390×844、390×430 和 844×390；确认页按钮随正文滚动，避免覆盖必选内容，原 Sheet 沿用既有固定操作区。
- [Node 复核](../../tmp/regression-20261109/pwa-final-node.log) 331/331，[Vite 构建](../../tmp/regression-20261109/pwa-final-build.log) 通过。原有大 chunk 提示仍在；没有新增运行时依赖或后端接口，也没有重复运行无变更的 Python 全套。
- [全量浏览器最终日志](../../tmp/regression-20261109/pwa-final-browser-all.log) 103/103（Chromium 101、Firefox 基础 2）。之后对认证子路由保留再补定向断言，并目视修正该路由的短屏滚动边缘，专项仍为 11/11，Node 331/331、构建和本地 Nginx 接收重新通过。首轮全量曾为 101/102，嵌套报告用例点击视口外指标超时，日志同时包含页面重新导航；没有改动该原测试断言，后续全量通过。[首次日志](../../tmp/regression-20261109/pwa-browser-all.log) 和 [原失败证据](../../tmp/regression-20261109/pwa-all-artifacts) 保留，不据此宣称所有历史 Grill 问题均已修复。
- [构建产物真实接收摘要](../../tmp/regression-20261109/pwa-built-smoke.json)、[脚本](../../tmp/regression-20261109/pwa-built-smoke.mjs)、[截图](../../tmp/regression-20261109/pwa-built-pc.png)：使用临时本地 Nginx 只读挂载 dist 和当前配置，在 127.0.0.1:4175 验证 manifest、图标、脚本 MIME／no-cache、无控制器时 303 回退、真实 Service Worker 接收和 Sheet 移交。业务鉴权／上下文为 fixture，业务 POST=0，CacheStorage=[]，Chromium installabilityErrors=[]。临时容器验证后停止，原开发前端和 API 继续运行。

关键命令：

```powershell
npm --prefix web-ui test
npm --prefix web-ui run build
cd web-ui
npx playwright test e2e/pwa-share-import.spec.js --project=chromium --reporter=line
npx playwright test --reporter=line
```

## 未覆盖边界

完成本地代码、真实浏览器接收和构建配置验证，不代表完成 Android 系统分享菜单／安装的真机验收，也未验证 iPhone 分享接收。没有部署生产，因此不会让线上手机入口自动出现。真实 OCR 与登录受限招聘站沿用第一阶段边界；多图、完整职位搜索和原 Grill R01–R12 不在本阶段。
