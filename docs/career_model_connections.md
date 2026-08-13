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
4. Key 在进入 PostgreSQL 前由服务端 `pyca/cryptography` 的 Fernet 加密；任何列表、读取接口、日志和编辑页都不会返回明文。

默认无需手动配置主密钥：服务首次启动会自动在 `data/career_credential_master.key` 创建一把 Fernet 主密钥，并在后续本地重启时复用。生产 Docker 环境中该文件位于持久化的 `application_data` 卷，因此容器更新不会使已经保存的模型 API Key 失效。

如果后续接入云 Secret 管理服务，也可以显式设置下列环境变量；显式值优先于自动托管文件：

```dotenv
CAREER_CREDENTIAL_MASTER_KEY=<Fernet.generate_key() 生成的 URL-safe Base64 值>
```

不要把任意口令、数据库密码或模型 API Key 直接填入此变量。无论使用自动托管文件还是显式 Secret，遗失、删除或更换主密钥后，旧 API Key 都无法解密，只能从页面重新输入。

### 旧明文凭据迁移边界

`20260810_08` 以前，本机数据库的 `plaintext_api_key` 可能仍保存了旧 Key。新版本不会再写入该列：只要页面保存了 API Key，数据库会写入 `encryption_scheme=fernet_v1` 和 Fernet token，并把旧明文列清空。

上线前按以下顺序处理：

1. 执行 `alembic upgrade head`。
2. 执行 `python scripts/migrate_career_legacy_credentials.py`；脚本会复用或自动创建同一把持久化主密钥，只输出迁移数量，不输出 Key、档案名或组织信息。
3. 脚本提示剩余旧明文为 `0` 后，确认 `CAREER_ALLOW_LEGACY_PLAINTEXT_CREDENTIALS` 未设置或为 `false`，再启动 API 服务。

本地临时排障时可显式设置 `CAREER_ALLOW_LEGACY_PLAINTEXT_CREDENTIALS=true` 读取旧明文，但它不是生产配置，不能替代迁移。迁移 `20260806_02` 曾预留但未定义格式的历史 `encrypted_api_key` 会标记为 `legacy_unknown`；系统不会将其误当成 Key 使用，必须在页面重新填写并保存。

调用链：`请求求职模块 → ensure_credential_master_key() → 保存模型连接 → CareerModelProfileRepository.upsert_profile() → CredentialCipher.encrypt() → PostgreSQL`；实际对话则通过 `ModelGateway → read_stored_credential() → CredentialCipher.decrypt()` 在进程内短暂读取，随后交给同一个模型调用适配器。Fernet 密钥不存 PostgreSQL、不传浏览器，也不进入可观测性日志；自动托管模式只保存在服务端持久化目录。

## 支持范围

当前连接层统一使用 OpenAI-compatible Chat Completions 协议。服务商若不兼容该协议，不能直接添加为求职助手模型连接。

## 官方免费模型选择

聊天输入框旁的模型下拉只展示 `readiness=ready` 的真实可调用连接；免费连接额外以“【免费】”标记。尚未接入的候选模型不会再混入聊天下拉，而是统一放在“申请免费模型”目录中。管理员可从目录打开 API Key 申请、官方接入文档和费用说明，并一键预填连接参数；只有完成真实连通性测试并保存后，连接才会进入聊天下拉。免费不代表匿名调用：云端 API 仍需由平台管理员注册账号并申请 API Key。

模型主标签统一使用“`Provider · Model ID`”，连接的自定义显示名称仅作为辅助说明。因此，即使两个连接都被命名为“DeepSeek 模型连接”，`deepseek-v4-pro` 与 `deepseek-v4-flash` 也会被明确区分。免费目录请求失败时会单独提示并支持重试，不会阻断会话历史和已配置连接的加载。

管理员把 Key 测试并保存后，Key 仅保留在服务端，访客不需要自行配置 Key。免费优先路由只会选择状态为“可调用”的免费连接，不会把 DeepSeek、Qwen Plus 等按量付费模型误当作免费模型。

当前目录与官方接入入口：

| 服务商 | 免费方式 | API Base URL | Key 申请 | 接入/额度说明 | 费用与实时目录 |
| --- | --- | --- | --- | --- | --- |
| Google Gemini | 标准 Free Tier；地区和项目级限额以 AI Studio 为准 | `https://generativelanguage.googleapis.com/v1beta/openai` | [创建 Key](https://aistudio.google.com/apikey) | [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai) | [Pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| OpenRouter | `openrouter/free` 动态路由；未购额度账号为 50 次/日、20 RPM | `https://openrouter.ai/api/v1` | [创建 Key](https://openrouter.ai/settings/keys) | [Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router) | [Pricing](https://openrouter.ai/pricing) |
| ModelScope | 带魔搭标识的 API-Inference 模型至少 50 次/日，热门模型可能另有限额 | `https://api-inference.modelscope.cn/v1` | [Access Token](https://modelscope.cn/my/myaccesstoken) | [API-Inference 文档](https://modelscope.cn/docs/model-service/API-Inference/intro) | [免费调用说明](https://www.modelscope.cn/learn/434367) |
| 硅基流动 SiliconFlow | 实名认证后可用免费模型；带 `Pro/` 前缀的同系列模型收费 | `https://api.siliconflow.cn/v1` | [创建 Key](https://cloud.siliconflow.cn/account/ak) | [快速开始](https://docs.siliconflow.cn/cn/userguide/quickstart) | [模型定价](https://siliconflow.cn/pricing) |
| NVIDIA NIM | Build 免费托管端点仅供开发和试用，不允许作为生产额度使用 | `https://integrate.api.nvidia.com/v1` | [创建 Key](https://build.nvidia.com/settings/api-keys) | [NIM LLM API](https://docs.api.nvidia.com/nim/reference/llm-apis) | [实时模型目录](https://build.nvidia.com/explore/discover?api-key=true) |
| 阿里云百炼 Qwen | 中国内地（北京）新用户限时额度；`qwen3.6-flash` 当前为 100 万 Token、90 天 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | [创建 Key](https://bailian.console.aliyun.com/cn-beijing/?tab=app#/api-key) | [新人免费额度](https://help.aliyun.com/zh/model-studio/new-free-quota/) | [模型定价](https://help.aliyun.com/zh/model-studio/model-pricing) |

目录中的模型 ID 是经过核对的调用标识，但“有这个模型”不等于“当前账号仍有免费额度”。ModelScope、OpenRouter 和 NVIDIA 的可用模型会动态变化，管理员保存连接前必须以官方实时目录为准并执行真实连通性测试。硅基流动的 Qwen 3 8B 调用标识是 `Qwen/Qwen3-8B`（不是 `Qwen/Qwen-3-8B`）；NVIDIA 目录使用当前仍明确提供免费端点的 `meta/llama-3.3-70b-instruct`，不再预置状态不稳定的 Stockmark 端点。Gemini 3.5 Flash-Lite 支持图片输入，因此目录会将其标记为 Vision 模型。

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
