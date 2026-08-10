# 求职助手模型连接配置

“模型与连接”使用一张可滚动的配置页：先选择服务商，再向下填写连接名称、模型 ID、官网地址、API Key、请求地址和模型能力。

## 预置服务商

已内置 DeepSeek、Groq、OpenRouter、Google Gemini、阿里云百炼 Qwen、SiliconFlow、ModelScope、NVIDIA NIM、腾讯云 TokenHub、百度千帆、腾讯混元、智谱 AI、MiniMax 与火山方舟。选择预置项会自动填充官网地址和 OpenAI-compatible API Base URL；也可以选择“自定义兼容服务商”手动填写。

DeepSeek 的两个地址用途不同：

- 官网地址：`https://platform.deepseek.com`
- 请求地址：`https://api.deepseek.com/v1`

请求地址必须填写到 API Base URL 层级，不能拼接 `/chat/completions`；生产环境必须使用 HTTPS。

## API Key 与连通性

API Key 在页面中显式填写，但仅用于当前测试和服务端加密保存：

1. 点击“测试连接”后，服务端发起一次最小真实推理请求，验证地址、模型 ID 和 API Key。
2. 测试成功才会启用“保存模型连接”。
3. 保存时会再次验证，避免在测试后修改字段导致不可用连接入库。
4. Key 通过 PostgreSQL `pgcrypto` 以 AES-256 加密保存；任何列表、读取接口、日志和编辑页都不会返回明文。

在 `.env.career-assistant` 中一次性配置：

```dotenv
CAREER_CREDENTIAL_MASTER_KEY=replace-with-a-long-random-secret
```

生产环境应使用独立的随机长字符串并放入部署平台的 Secret 管理功能。遗失这个值后，旧 API Key 无法解密，只能从页面重新输入。

## 支持范围

当前连接层统一使用 OpenAI-compatible Chat Completions 协议。服务商若不兼容该协议，不能直接添加为求职助手模型连接。

## 官方免费模型选择

聊天输入框旁的模型下拉列表直接展示真正的免费模型，并以“【免费】”标记。尚未由管理员接入的候选项显示为“【免费·待接入】”且不可选择；已通过连通性测试、保存为 `free_quota` 的连接会变成可选项。免费不代表匿名调用：云端 API 仍需由平台管理员注册账号并申请 API Key。

管理员把 Key 测试并保存后，Key 仅保留在服务端，访客不需要自行配置 Key。免费优先路由只会选择状态为“可调用”的免费连接，不会把 DeepSeek、Qwen Plus 等按量付费模型误当作免费模型。

当前目录与接入入口：

| 服务商 | 免费方式 | API Base URL | 申请入口 |
| --- | --- | --- | --- |
| Google Gemini | Gemini Developer API Free Tier | `https://generativelanguage.googleapis.com/v1beta/openai` | <https://aistudio.google.com/apikey> |
| OpenRouter | `openrouter/free` 免费模型路由 | `https://openrouter.ai/api/v1` | <https://openrouter.ai/keys> |
| ModelScope | 选定开源模型免费日额度 | `https://api-inference.modelscope.cn/v1` | <https://modelscope.cn/my/myaccesstoken> |
| 硅基流动 SiliconFlow | 实名认证后可调用标记为免费的开源模型；带 `Pro/` 前缀的是付费版本 | `https://api.siliconflow.cn/v1` | <https://cloud.siliconflow.cn/account/ak> |
| NVIDIA NIM | 部分模型免费原型端点 | `https://integrate.api.nvidia.com/v1` | <https://build.nvidia.com> |
| 阿里云百炼 Qwen | 新用户限时免费额度，适用于云端 Vision | `https://dashscope.aliyuncs.com/compatible-mode/v1` | <https://bailian.console.aliyun.com> |

### 2026-08-07：上线优先的云端 Vision 选择

求职助手的生产请求不会依赖开发电脑的 NVIDIA GPU 或本地 Ollama。图片简历、职位截图与面经图片的云端语义理解默认推荐使用 `qwen2.5-vl-7b-instruct`：它支持图片输入和多轮求职对话，可通过百炼 OpenAI-compatible Chat Completions 接口接入。

在“模型与连接”中选择“阿里云百炼 Qwen”后填写：

- 模型 ID：`qwen2.5-vl-7b-instruct`
- API Key：从百炼控制台创建的 `DASHSCOPE_API_KEY`
- API Base URL：优先使用百炼当前工作空间在控制台显示的 OpenAI-compatible 地址；旧版通用地址为 `https://dashscope.aliyuncs.com/compatible-mode/v1`。
- 能力：勾选“文字与 PDF 文本”和“图片简历”。

`qwen3.5-ocr` 是更适合文本、表格和证照字段抽取的专用 OCR 模型。它不作为普通会话模型列出：官方文档说明其使用固定内部 System Message，且不适合承接通用连续聊天。后续会由独立的云端 OCR 复核节点调用；这样不会让用户误把 OCR 模型选成聊天模型。

百炼的免费额度具有账户、区域、模型准入和有效期条件，不能被标注为“永久免费”。系统只会在真实连接测试成功后，将其标记为平台可用。

DeepSeek、Groq、腾讯云 TokenHub、腾讯混元、百度千帆、阿里云百炼、火山方舟、Moonshot 与 MiniMax 都保留在“模型与连接”的手动配置项中。它们可能提供新用户赠送、免费体验或限时 Token 包，但并不等同于平台可持续的免费模型，因此不会被自动免费路由选中，也不会显示为“【免费】”。

科大讯飞 Spark Lite 使用 WebSocket 鉴权（AppID、APIKey、APISecret），不属于当前统一的 OpenAI-compatible 调用器；在专用 Spark 客户端完成前，不会把它伪装成可用的通用模型连接。

GitHub Models 不在目录中：GitHub 已于 2026-07-30 停止其模型目录和推理 API，现有 GitHub Token 仅继续服务于 GitHub 项目检索。
