# 生产 Skill 种子

本目录是部署镜像中唯一允许携带的 Skill 快照来源。生产服务器不会读取开发电脑的
`~/.agents`、`~/.codex` 或 Codex 插件缓存。`.skill-lock.json` 只保存已经核验的公开
GitHub 仓库来源，供 Star 周快照使用；来源不明确的本地 Skill 不建立仓库映射。

当前快照包含 **59 个去重 Skill**。每项携带 `SKILL.md`，需要时附带 `references/`
内的 `.md/.json/.txt` 只读资料与许可证；目录名称按frontmatter的`name`规整，详情与
附带文件校验值见 `catalog.manifest.json`。其中包括项目正在使用的
`find-skills`、生图、文档、前端设计、求职材料解析等 Skill 说明，但不携带其插件、CLI、
账户配置、API Key、缓存或任何二进制依赖。

这些文件仅供本平台的“技能库”查看、编辑与检索提示词使用；它们不携带原始插件、CLI、
账户配置或 API Key，也不会在服务器上自动执行其中的命令。

## 更新流程

需要按目录手动导出时，在开发机先审查后运行：

```powershell
.\.venv\Scripts\python.exe scripts\export_portable_skills.py `
  --source-root "$env:USERPROFILE\.agents\skills" `
  --destination-root deploy\skill-seeds
```

需要同步本机当前可见的全部 Skill 时，运行：

```powershell
.\.venv\Scripts\python.exe scripts\export_portable_skills.py --local-catalog
```

脚本会按 Skill `name` 去重，优先级是 `~/.agents/skills`、`~/.codex/skills`、插件缓存；
默认不会覆盖已经审查并提交的种子。只有确认上游变更可接受时才加 `--overwrite`。无论哪种
模式，脚本只复制提示词、支持的只读引用与许可证，不会复制脚本、`node_modules`、二进制工具、缓存或包含凭据的本地
目录。更新后执行 `scripts/verify_skill_portability.py`，检查并提交本目录的差异。

`resume-builder`固定到公开仓库提交`19a4133e326b5f6b6ad09862b6b0f7b6002e1924`，保留MIT许可证
和schema参考；`tailored-resume-generator`为用户提供版本的项目适配。两者均可在主对话通过
Slash调用，也可同时挂载。安装Skill不会给聊天模型增加文件读取或Reactive Resume MCP能力；
JSON未进行目标schema校验时必须标为待验证草稿。

生产的 `skill-seed` 一次性容器会把这里的缺失文件增量复制到 `application_skills` 卷，
绝不会覆盖用户从 WebUI 保存的同名文件。若要将一个种子强制恢复为版本库内容，应先备份并
删除该卷中对应的 `SKILL.md`，再重新运行 `skill-seed`；不能直接用部署覆盖用户修改。
