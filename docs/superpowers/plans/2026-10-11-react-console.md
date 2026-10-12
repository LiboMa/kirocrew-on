# React 控制台重写 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 用 React 重写 KiroCrew ON 控制台（功能与现有原生 JS 控制台对齐，并以“查看与搜索为主”的会话监控为核心），Python 后端与 `/v1` API 不变。

**Architecture:** 前端源码在 `web/`（Vite + React + TypeScript + Tailwind v4 + shadcn/ui），构建到 `octopus/ui/dist/`（git 忽略，安装时在主机构建）。`octopus/server.py` 只调整静态文件服务：`/` 优先返回 React 构建（未构建时回退到旧版），`/assets/*` 为构建资源，旧版控制台保留在 `/classic/`（其脚本与样式沿用原根路径），`/crew-entry.html` 不变。数据层：类型化 API 客户端（Bearer token，sessionStorage `octopus.token`，与旧版共享登录），TanStack Query 缓存与轮询，fetch 流式 SSE（`after` 续传）驱动会话实时刷新。

**Tech Stack:** React 19、Vite 8、TypeScript、Tailwind CSS 4、shadcn/ui（Radix）、TanStack Query 5、lucide-react；Vitest + Testing Library；Playwright 浏览器测试沿用 8921 临时数据服务。

**Spec:** `docs/superpowers/specs/2026-10-10-kirocrew-on-design.md` §10（控制台：灵活、易用、以查看与搜索为主）；负责人 2026-10-11 决定：React 框架性重写、Python 后端不变；Vite + React + TS；Tailwind + shadcn/ui；新版在 `/`、旧版暂留 `/classic/` 直到功能对齐并经负责人确认；安装时在主机构建（仓库只放源码）。另见 `docs/superpowers/plans/2026-10-11-launch-wizard.md`（向导三种编排方式）。

## Global Constraints

- `/v1` API、数据库与 Worker 协议不改；后端只改静态文件服务。
- 与旧版共享登录态（sessionStorage `octopus.token`）；旧版功能在新版对齐前继续可用。
- 不把 token、原生入口链接写入日志、localStorage 或 URL（原生入口沿用 `/crew-entry.html` 弹窗流程）。
- 中文界面；窄屏（390 px）无横向溢出。
- 开发期只跑单元测试与受影响的浏览器测试；本地提交，不推送。

## Review Focus

1. 未构建前端（新克隆、CI 未构建）时 `/` 仍可用（回退旧版），`/assets/*` 不越界读取文件。
2. SSE 断线续传不重复、不丢事件；切换会话时旧流被取消，迟到事件不写入新会话。
3. 向导草稿与旧版互不覆盖；推荐/导入的迟到结果不覆盖用户已编辑任务。
4. 401 / Token 过期统一回到登录页；403 / 409 等业务错误显示服务端中文原因。
5. 构建产物路径固定、可重复（主机 `npm ci && npm run build`），离线回归包含前端构建与单测。

---

### Task R1: 脚手架与静态服务
`web/`（Vite React TS、Tailwind v4、shadcn 初始化与基础组件、Vitest）；`octopus/server.py` 静态服务（dist、/assets、/classic、回退）及 `tests/test_static_console.py`；`.gitignore`；`deploy/install/al2023.sh` 构建步骤；`tools/regress.sh` 前端构建与单测。

### Task R2: 数据层与外壳
类型定义（`web/src/api/types.ts`，按 `/v1` 实际响应）；API 客户端与错误；登录页；外壳（顶部栏：空间切换、用户菜单；侧栏：会话列表搜索与状态筛选、工作流库）。

### Task R3: 会话详情（监控为主）
标题与状态、需要处理的提示、任务与 Attempt 证据（实际后端 / 模型 / 原生 ID / 结论 / 错误）、事件时间线（SSE 实时）、恢复记录、Workspace 与原生会话信息；操作：续作、诊断恢复、打开原生对话、停止、关闭 / 重新打开、导出、删除（输入 ID 确认）、分配 Worker。

### Task R4: 发起开发向导
需求 → 编排方式（参考模板 / KiroCrew 推荐 / 导入已生成）→ 任务与 Agent → 确认；按需求匹配（`/v1/workflows/resolve`）、推荐（`/v1/designs` 轮询、采用）、导入（`/v1/workflows/parse`）；草稿按用户 / 空间 / 来源会话保存。

### Task R5: 工作流库
列表、编辑（新版本）、导入 / 导出 JSON、分享（用户 / Team，viewer / runner / editor）、归档、由工作流发起。

### Task R6: 设置
Team 管理（成员增删）、API Token（创建一次性显示、撤销）、Worker 设置（管理员：登记、启停、能力）、退出登录。

### Task R7: 浏览器测试与文档
`tests/react-console.browser.js`（8921 临时数据服务）；旧版浏览器测试改为访问 `/classic/`；README / OPERATIONS / MVP-RELEASE 更新（D19–D22）。
