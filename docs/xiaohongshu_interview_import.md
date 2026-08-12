# 小红书面经公开导入

## 目标

在“面经库”页面通过“自动采集 → 小红书导入”提交一个小红书链接。服务端会尽力读取该链接中**对无登录请求实际公开可读取**的笔记：提取正文、下载多张配图并走现有 OCR/文档解析、交给已配置的文本模型判断有效性，最后将满足条件的内容写入既有面经库。

“关键词采集”同样是可执行入口：后端先生成 `https://www.xiaohongshu.com/search_result?keyword=...`，再交给同一套导入编排。它不是“需要授权连接器”的占位任务；公开搜索页没有暴露笔记列表时，会以 `source_content_unavailable` 结束并说明原因。

面经库不会保存原始小红书图片或浏览器 Cookie。候选记录会保存正文、OCR 文本、分析结果、来源链接、失败原因和最终入库的面经 ID，便于追溯。

## 调用链

```text
POST /api/career/interview-library/xiaohongshu-imports
  → InterviewCollectionService.create_xiaohongshu_import_job()
  → FastAPI BackgroundTasks.run_xiaohongshu_import()
    → XiaohongshuPublicSourceAdapter.collect()
    → XiaohongshuPublicSourceAdapter.download_images()
    → TemporaryAttachmentStore.save_bytes()
    → AttachmentParser.parse()
    → InterviewEvidenceAnalyzer.analyze()
    → InterviewLibraryService.ingest()

POST /api/career/interview-library/collection-jobs (platform_key=xiaohongshu)
  → InterviewCollectionService.create_xiaohongshu_keyword_import_job()
  → FastAPI BackgroundTasks.run_xiaohongshu_import()
  → 复用上述公开导入链路
```

前端每 1.5 秒读取任务详情，并展示 `discover → fetch → ocr → analyze → import` 的进度和统计。正常执行过程中，无论入口读取成功、单篇失败还是模型不可用，任务都会进入明确的 `SUCCEEDED` 或 `FAILED` 终态。仓储通过 `queued → running` 的原子抢占保证同一个任务不会被多个后台调用重复执行。

FastAPI `BackgroundTasks` 不是持久化队列：若整个服务进程在执行中被强制终止，运行中的任务不会自动续跑。此类“进程崩溃后恢复”需要另行接入持久化 worker / 租约恢复机制，当前功能不会伪造已完成结果。

## 输入与边界

- 接受小红书单篇笔记、个人页、收藏页、搜索页链接；每次限制为 1–50 条。是否能够实际导入取决于小红书是否把内容返回给无登录服务端请求。
- 单篇笔记：当页面返回可解析的公开 HTML 或公开初始状态时，读取标题、正文和公开配图。
- 列表页：只读取其 HTML/公开初始状态中实际暴露的笔记链接，再逐篇读取。
- 页面如果需要登录、验证码，或仅在浏览器 JavaScript 运行后才加载列表，任务会以可读错误结束；小红书将笔记重定向到 `error_code=300031` 限制页时也会明确标识为访问受限。导入不会使用 Cookie、账号、验证码绕过或私有接口伪造读取成功。
- 小红书部分旧搜索路径会先返回同站 `http` 跳转。适配器会在内存中直接升级回同站 HTTPS，不会真的请求中间 HTTP 地址。
- 图片字节只进入临时附件和 OCR 流程，任务结束后清理；数据库只保存 OCR/分析后的文本和轻量元数据。

## 自动入库规则

模型只有在同时满足下列条件时才自动写入面经库：

1. 判定为有效面经；
2. 置信度不低于 0.60；
3. 可以抽取公司、岗位和至少一个具体问题。

未通过的内容不会丢失：无效内容标记为“已过滤”，模型暂不可用或字段不全的内容保留为候选，供后续检查或手动处理。不同来源笔记会使用来源内容哈希区分，避免同公司同岗位的多篇面经互相覆盖。

## 验证命令

在项目根目录运行：

```powershell
& .\.venv\Scripts\python.exe scripts\verify_xiaohongshu_collection.py
& .\.venv\Scripts\python.exe scripts\verify_xiaohongshu_import_orchestration.py
& .\.venv\Scripts\python.exe scripts\verify_xiaohongshu_import_api.py
& .\.venv\Scripts\python.exe scripts\verify_interview_collection_metadata_contract.py
cd web-ui
npm run build
```

其中回归覆盖：公开列表和单篇笔记解析、公开初始状态解析、多图下载限额、同站 HTTPS 跳转、登录/验证码/访问受限页面、单篇失败不中断、无效内容过滤、有效内容自动入库、同一任务重复后台调用只会被一个执行者抢占、源页不可读取时进入 `FAILED` 终态，以及临时图片不进入候选元数据。
