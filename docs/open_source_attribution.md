# 项目开源归属说明

## 2026-11-09：BOSS 入站消息读取

参考 [can4hou6joeng4/boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli/tree/b6e5a0aca58bb67116ed0cfc32febed253802092)（MIT，固定提交 `b6e5a0aca58bb67116ed0cfc32febed253802092`）的求职端联系人字段和列表路由，以及 [jackwener/OpenCLI](https://github.com/jackwener/OpenCLI/tree/97dc0cd5214b9602bcda78d7422f231ee8a803e9)（Apache-2.0，固定提交 `97dc0cd5214b9602bcda78d7422f231ee8a803e9`）的求职端历史消息读取参数。

本项目自行实现 MV3 页面读取、归一化、平台持久化与收件页，不引入两者整套程序或招聘者端发送代码。具体采用字段、调用链和验证边界见 [BOSS 收件模块](modules/boss-inbox.md)。

## 2026-11-04：简历构建Skill

安装来源：[resume-builder](https://skills.sh/reactive-resume/reactive-resume/resume-builder)，仓库
`reactive-resume/reactive-resume`，固定提交`19a4133e326b5f6b6ad09862b6b0f7b6002e1924`，目录
`plugins/reactive-resume/skills/resume-builder`。上游MIT许可证（Copyright 2026 Amruth Pillai）
保留在项目副本及部署种子的`resume-builder/LICENSE`；保留上游schema参考，追加中文运行边界。
本次只安装提示词和只读资料，没有集成Reactive Resume整套应用、MCP或在线导入验证。

线上笔试模块没有直接安装候选项目的整套应用。页面提取、状态机、API、执行适配和归档均在本项目中重新实现；以下项目用于选择器、权限和交互思路参考。

| 项目 | 审查版本 | 许可证 | 参考或改造范围 |
|---|---|---|---|
| [RaheesAhmed/LeetCode-AI-Assistant](https://github.com/RaheesAhmed/LeetCode-AI-Assistant) | `836856296b9ebb7801cec7e7e89b5f008dcd9745` | MIT，Copyright (c) 2025 Rahees Ahmed | LeetCode 题面、语言、代码编辑器和公开样例的选择器思路 |
| [harry-the-nerd/open-interview-assistant](https://github.com/harry-the-nerd/open-interview-assistant) | `cbe239d6881d4967d8e8322a3e5d9a56cabb8016` | Apache-2.0 | `activeTab` 用户手势、站点选择器逐级回退、受限消息契约 |
| [LeetBuddyAI/LeetBuddy](https://github.com/LeetBuddyAI/LeetBuddy) | `8fa99e14aa7496f6438ffebcccf6d67b283c3e24` | MIT，Copyright (c) 2025 Nicholas Jano | 单题会话、复杂度、边界用例与图片补充的交互思路 |
| [zubyj/leetcode-explained](https://github.com/zubyj/leetcode-explained) | `17d2ee03fb8fcb408a306bbd627e75c1cda684b6` | MIT | MV3 文件组织与页面变化适配思路 |
| [engineer-man/piston](https://github.com/engineer-man/piston) | 容器摘要 `sha256:2f66b7…127a` | MIT | 本地隔离代码执行服务；作为独立容器使用，未复制其源码 |

`browser-extension/job-library/assessment-capture.js` 文件头保留了直接相关的来源和许可证标记。上述项目的完整许可证文本以各链接版本中的 `LICENSE` 为准；任何再分发仍应同时遵守对应许可证。

明确未采用的部分包括：扩展直连模型、在第三方页面写入或提交答案、Electron 隐身覆盖层、候选项目的 API Key 管理、Redis/Express 后端和远程现成答案服务。
