# 生产 Skill 种子

本目录是部署镜像中唯一允许携带的 Skill 快照来源。生产服务器不会读取开发电脑的
`~/.agents`、`~/.codex` 或 Codex 插件缓存。

当前快照仅包含项目已经实际使用、且由用户安装到 `~/.agents/skills` 的四个 Skill：

- `find-skills`：技能库的 GitHub 开放 Skill 检索策略说明；
- `ai-image-generation`：生图提示词与模型选择参考；
- `og-image-design`：公众号/社交分享图的排版参考；
- `grill-me`：方案审视辅助说明。

这些文件仅供本平台的“技能库”查看、编辑与检索提示词使用；它们不携带原始插件、CLI、
账户配置或 API Key，也不会在服务器上自动执行其中的命令。

## 更新流程

在开发机先审查需要携带的 `SKILL.md`，再显式运行：

```powershell
.\.venv\Scripts\python.exe scripts\export_portable_skills.py `
  --source-root "$env:USERPROFILE\.agents\skills" `
  --destination-root deploy\skill-seeds
```

默认不会覆盖已经审查并提交的种子；只有确认上游变更可接受时才加 `--overwrite`。不要把
Codex 的 `.system` Skill、插件缓存、`node_modules`、二进制工具或包含凭据的本地目录直接
复制进本目录。更新后执行 `scripts/verify_skill_portability.py`，检查并提交本目录的差异。

生产的 `skill-seed` 一次性容器会把这里的缺失文件增量复制到 `application_skills` 卷，
绝不会覆盖用户从 WebUI 保存的同名文件。若要将一个种子强制恢复为版本库内容，应先备份并
删除该卷中对应的 `SKILL.md`，再重新运行 `skill-seed`；不能直接用部署覆盖用户修改。
