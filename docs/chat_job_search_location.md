# 对话内职位搜索的城市定位

## 目标与调用链

用户明确输入的工作城市优先使用。未提供城市时，对话内求职组件尝试读取设备当前位置，并将其匹配为现有 BOSS 城市目录中的城市；无法匹配时允许手动选择，不使用独立职位库的历史缓存城市或默认上海兜底。

调用链为 `JobSearchWorkspace.vue → resolveJobSearchCity → navigator.geolocation → 浏览器直接请求 BigDataCloud → matchLocatedCity → BOSS 城市对象`。定位成功仅发布城市名、来源 `location` 等搜索条件，不发布经纬度。

## 实现与权限

- 实现文件：`web-ui/src/job-search-location.js`；验证文件：`web-ui/src/job-search-location.test.js`。
- `resolveJobSearchCity({ cities, requestPermission: false })` 先查询浏览器 `geolocation` 权限。已有 `granted` 授权时自动定位；`prompt`、`denied` 或不支持 Permissions API 时不自动读取坐标，也不向外部服务发送坐标。
- 用户点击“启用定位服务”后以 `requestPermission: true` 请求设备当前位置，由浏览器执行权限询问。浏览器已拒绝时提示用户在浏览器中允许定位，或直接手动选城。
- 设备定位最多等待 8 秒，`maximumAge: 0`，不读取应用保存的旧坐标；逆地理请求最多等待 10 秒。
- 默认服务是浏览器端 `https://api.bigdatacloud.net/data/reverse-geocode-client`，参数为本次坐标和 `localityLanguage=zh`。请求不携带应用 Cookie，并设置 `referrerPolicy: no-referrer`。
- 结果只使用返回的 `city` 匹配招聘城市目录，支持移除“市”等行政后缀。无有效城市、网络失败、权限失败、浏览器不支持定位时返回 `city: null` 和中文提示。
- 经纬度仅在本次设备定位和请求过程中临时存在，不写入本项目数据库、localStorage、对话消息或后端日志。**本项目不提供逆地理代理接口，不需要新增后端位置路由，也不需要 API Key。**

返回契约：`{ city, status, message, attribution, attributionUrl }`，其中状态为 `located / permission_required / denied / unavailable`，`city` 是输入城市目录中匹配的原始对象或 `null`。

## 服务选择与官方来源

BigDataCloud 官方说明允许免费客户端接口由浏览器或移动设备直接调用，前提是使用用户同意后的当前设备坐标；禁止由服务器代理，也禁止用预先保存或第三方坐标进行查询。因此这里采用浏览器直连，未引入后端代理、批量地理编码或周期定位任务。

供应商公开说明：其免费模式会匿名聚合请求坐标与网络 IP 的配对信号，以改进和验证 IP 定位数据。界面在启用定位按钮旁标明由 BigDataCloud 解析当前位置，并提供手动选城；定位状态附有供应商链接。项目没有承诺供应商完全不处理或不留存其接收的数据。

官方来源：

- [免费客户端逆地理服务的工作方式与公平使用要求](https://www.bigdatacloud.com/docs/article/why-is-reverse-geocoding-api-free)
- [客户端接口使用的 API 域名](https://www.bigdatacloud.com/docs/api-domains)
- [免费接口不需要 API Key，但仅限客户端使用](https://www.bigdatacloud.com/docs/authentication)

本轮对比过 Photon 与 Nominatim。Photon 官方允许合理低频项目使用，但当前环境访问其公开端点失败；Nominatim 的公开实例有专门使用政策和应用整体限速要求。因此最终没有把它们作为本功能的默认依赖，也没有保留对应试验路由。

## 验证结果与边界

2026-11-04 执行 `node --test web-ui/src/job-search-location.test.js`，10 项通过，覆盖未授权不请求、已有授权定位、主动启用、拒绝与超时、Permissions API 缺失、服务失败、城市匹配失败、不支持定位和禁用历史坐标缓存。

外部接口连通性检查返回 HTTP 200，响应包含中文城市字段；该检查只能说明接口网络可达与字段可解析，**不等同于真实用户设备定位验收**。本轮未读取用户实际 GPS，也未在真实浏览器中完成“定位授权 → 真实城市 → 真实岗位搜索”的端到端验收。

浏览器是否能取得位置取决于设备、系统定位服务、浏览器权限和安全上下文；外部服务网络和城市覆盖也可能失败。以上情况均通过中文提示和手动选城完成该步骤，不静默猜测城市。
