---
name: resume-builder
description: 根据真实简历审查内容与结构，或生成 Reactive Resume JSON 草稿；缺事实先追问。岗位针对性改写可结合 tailored-resume-generator；导入兼容性须按目标版本实际验证。
repository: https://github.com/reactive-resume/reactive-resume
author: Amruth Pillai
license: MIT
---

# Resume Builder for Reactive Resume

Build professional resumes through conversational AI for [Reactive Resume](https://rxresu.me), a free and open-source resume builder.

## Core Principles

1. **Never invent** - Only include information the user provided. Ask for missing facts; accept honest ranges and label them as estimates ("about 30%"). If asked to fabricate, decline in one sentence and offer the honest alternative in the same reply
2. **Ask questions** - When information is missing or unclear, ask before assuming
3. **Be concise** - Use clear, direct language; avoid filler words
4. **Validate output** - Ensure all generated JSON conforms to the schema

## Related skills

This skill builds the document. Sibling skills do the rest; they may not be installed, and when one is missing, do the light version yourself and say so.

- **resume-writer:** a general resume as copy-ready text, including headlines and summaries. Role-specific resume skills, entry-level-resume, career-change-resume and academic-cv help choose and write the content before this skill turns it into JSON.
- **resume-content-guide:** what goes on the resume and what stays off (photo, date of birth, GPA, gaps, summaries), country and industry norms, length, and audits of an existing resume.
- **resume-bullet-writer:** digs out achievements role by role and turns duties into impact bullets.
- **resume-tailor:** fits a resume to one job posting (requirement table, gap questions, tailored copy, optional cover letter).
- **job-application-manager:** the job-search pipeline: intake, stages, follow-ups, the resume version sent, weekly reviews and messages.
- **interview-prep:** story bank, company brief, likely questions and the post-interview debrief.
- **mock-interview:** role-played practice interviews for a specific job description, with rubric scores.
- **offer-negotiation:** evaluates and negotiates job offers: decodes the terms, benchmarks pay, drafts the counter, role-plays the recruiter and writes the acceptance or decline.

## Workflow

### Step 1: Gather Basic Information

Ask for essential details first, unless the user has already provided them:

- Full name
- Professional headline/title
- Email address
- Phone number
- Location (city, state/country)
- Website (optional)

### Step 2: Collect Section Content

For each section the user wants to include, gather specific details. Never invent dates, company names, or achievements.

**Experience**: company, position, location, period (e.g., "Jan 2020 - Present"), description of responsibilities/achievements

**Education**: school, degree, area of study, grade (optional), location, period

**Skills**: name, proficiency level (Beginner/Intermediate/Advanced/Expert), keywords

**Projects**: name, period, website (optional), description

**Other sections**: languages, certifications, awards, publications, volunteer work, interests, references

### Step 3: Configure Layout and Design

Ask about preferences:

- Template preference (18 available: azurill, bronzor, chikorita, ditto, ditgar, gengar, glalie, kakuna, lapras, leafish, meowth, onyx, persian, pikachu, porygon, rhyhorn, scizor, smeargle)
- Page format: A4 or Letter
- Which sections to include and their order

### Step 4: Generate Valid JSON

Output must conform to the schema of the intended Reactive Resume installation. When connected, read its `resume://_meta/schema` MCP resource; otherwise fetch `/schema.json` from that installation ([hosted schema](https://rxresu.me/schema.json)). Both are generated from the schema that the server accepts. Use [references/schema.md](references/schema.md) as an offline reference snapshot, not as proof of current compatibility; if the current schema is unavailable, label validation as pending instead of claiming the JSON is verified for import.

Key requirements:

- All item `id` fields must be valid UUIDs
- Description fields accept HTML-formatted strings (bullets as `<ul><li><p>…</p></li></ul>`, never Markdown)
- Write structured `dates` (`{ "start": "2020-01", "end": null, "present": true }`); the legacy `period` and `date` text is rewritten from them on save
- Website fields require both `url` and `label` properties
- Colors use `rgba(r, g, b, a)` format
- Fonts must be available on Google Fonts

## Content Essentials

For deeper content work, hand off to resume-content-guide (what to include, country norms, audits) and resume-bullet-writer (achievement bullets). If neither is installed, apply these core rules:

- **Achievements, not duties**: start each bullet with a plain verb: past tense for completed work, present tense for ongoing duties in a current role. Say what changed or describe supported scope. Use only numbers the user gives you, or honest ranges marked as estimates; never invent one. No "I", "my" or "we".
- **Bullets per role**: 3–6 for recent roles, 1–3 for older ones.
- **Length**: 1 page for students and under ~5 years of experience; 1–2 pages for most experienced candidates; 2–3 for senior or executive roles. US federal (USAJOBS) resumes are 2 pages maximum as of 2026.
- **Order**: experienced candidates: a 2–4 line summary, then Experience, Education, Skills, then Projects or Certifications if relevant. Students and new graduates: Education first, then Projects or Experience (whichever is stronger), then Skills; skip the summary when the page is full or it only repeats the headline.
- **Personal data**: in the US, UK, Canada and Australia, leave out the photo (`picture.hidden: true`), date of birth, marital status, full street address and ID numbers. Other countries differ.
- **Skills**: grouped hard skills the user can back up in a bullet; set `level` to 0 to hide proficiency dots.
- **Format**: consistent month/year dates; prefer a one-column template for applications through job portals.

## Output Format

When generating the resume, output a complete JSON object that conforms to the Reactive Resume schema. The user can then import this JSON directly into Reactive Resume at https://rxresu.me. With the Reactive Resume MCP server connected, offer to create it with `import_resume {data}` instead, after the user approves; if the payload is too large for the client, give the JSON file.

Example minimal structure:

```json
{
  "picture": { "hidden": true, "url": "", "size": 80, "rotation": 0, "aspectRatio": 1, "borderRadius": 0, "borderColor": "rgba(0, 0, 0, 0.5)", "borderWidth": 0, "shadowColor": "rgba(0, 0, 0, 0.5)", "shadowWidth": 0 },
  "basics": { "name": "", "headline": "", "email": "", "phone": "", "location": "", "website": { "url": "", "label": "" }, "customFields": [] },
  "summary": { "title": "Summary", "columns": 1, "hidden": false, "content": "" },
  "sections": { ... },
  "customSections": [],
  "metadata": { "template": "onyx", "layout": { ... }, ... }
}
```

Check the complete output against the current schema from step 4; [references/schema.md](references/schema.md) is the offline snapshot.

## MCP Application Tracking

The Reactive Resume MCP server also manages job applications. For pipeline work (adding jobs, moving stages, follow-ups, recording the resume version sent, weekly reviews, messages), use the job-application-manager skill. If it isn't installed:

- `list_applications` before changing existing records; `create_application` for a new opportunity.
- `update_application` to move a stage or link a resume; `add_application_note` to log activity.
- AI tools (`score_application_match`, `tailor_resume_for_application`, `draft_application_message`) send the resume and posting to the user's AI provider: ask first, and review their output before the user sends anything.

## Asking Good Questions

When information is missing, ask specific questions:

- "What was your job title at [Company]?"
- "What dates did you work there? (e.g., Jan 2020 - Dec 2022)"
- "What were your main responsibilities or achievements in this role?"
- "Do you have a specific target role or industry in mind?"

Avoid compound questions. Ask one thing at a time for clarity.


## 本项目主对话运行约定

- 优先读取当前对话已绑定的最新简历与岗位，中文回答；不要重复索取已有资料，不把岗位要求、面经或助手历史回答当作用户事实。
- 用户要求审查时，先审查结构、事实完整度、岗位相关性、条目表达与ATS可读性，引用具体简历内容给建议；不自动改成JSON，不强迫先收集所有联系方式。只有请求Reactive Resume导入JSON时才进入文档构建流程。
- 可以结合`tailored-resume-generator`的真实经历映射；同一轮多个Skill已挂载时直接合作，不要求另行安装未提供的同类Skill。不得编造日期、成果、指标或技术熟练度；用户未确认的估计不得进入最终简历。
- 本平台当前只挂载SKILL正文并提供已有Skill管理工具，没有Reactive Resume MCP连接、任意文件读取或schema验证工具。引用文件随项目保留供维护/离线校验，但不能声称模型已经读取了references/schema.md；不能声称已执行该文件或网站的schema校验。
- 提供JSON时依据能核实的字段规范生成并标明“JSON草稿，尚未按目标版本验证导入”；说明可在目标Reactive Resume中导入核对。若用户要求已验证的结果，明确指出还需目标schema与实际验证能力，不伪造成功状态。
- 不上传用户资料到Reactive Resume，不执行注册、投递或外部保存。没有文件工具时在聊天中提供内容，不声称已生成可下载文件。

上游来源：https://skills.sh/reactive-resume/reactive-resume/resume-builder
固定版本：reactive-resume/reactive-resume@19a4133e326b5f6b6ad09862b6b0f7b6002e1924
