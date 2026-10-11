# 控制台浏览器回归（React 与 /classic/）

`react-console.browser.js` 与 `mvp-*.browser.js` 是 Playwright run-code 函数，针对 `tests/mvp_browser_fixture.py`
（8921，临时 HTTP/SQLite，不连接 Worker、不调用 Coding 工具）。`/` 是 React 控制台，旧版测试访问 `/classic/`。

```bash
npm --prefix web run build
npm --prefix web run test:browser                         # 全部 7 个文件，每个文件独立的临时数据服务
npm --prefix web run test:browser -- tests/react-console.browser.js
```

运行器 `web/scripts/browser-tests.mjs` 使用 `playwright-core` 与本机 Chrome（不下载浏览器）；也可经 Playwright MCP
的 `browser_run_code_unsafe` 加载同一个文件（需先启动 `PYTHONPATH=. .venv/bin/python tests/mvp_browser_fixture.py`）。

# 任务向导浏览器回归

`workflow-wizard.browser.js` 是 Playwright `run-code` 入口函数，使用独立的无界面
Chrome 与内存浏览器上下文。页面资源来自本仓库，所有 `/api/` 调用均拦截为合成响应；
不会访问用户的浏览器存储、原生 Sessions、认证或 Coding Agent。

仓库根目录启动只读静态服务：

```bash
python3 build_workflow_ui.py
python3 -m http.server 8920 --bind 127.0.0.1 --directory ui
```

使用已安装的 Playwright CLI 执行：

```bash
playwright-cli run-code --filename=tests/workflow-wizard.browser.js
```

也可通过 Playwright MCP 的 `browser_run_code_unsafe` 加载同一个文件。
执行环境需已有 Playwright 浏览器连接和 Chrome；无须安装或登录 Coding 工具。
默认只返回测试结果。自定义运行器可用第二个参数
`{artifactRoot: "/绝对路径/evidence/workflow-wizard"}` 启用截图；目录由调用方指定，
测试源码不固定开发者本机路径。

覆盖同一工作流的不同 Session 独立草稿、切换与刷新、迟到生成回执、需求变更后禁止采用
旧结果、新 Session 空输入、统一编辑入口、旧静态路由兼容、独立幂等请求、全部模板、
保护手写目标、搜索和窄屏，以及源码准备四种状态、进度、重试和删除确认稳定性。
模拟的开始请求仅验证前端数据传递，不是原生执行证明。

不启动浏览器的存储回归：

```bash
node --test tests/workflow-editor-store.test.js
python3 build_workflow_ui.py --check
```

覆盖不同 Session / 标签页互不覆盖、生成 job 归属、旧全局数据只迁移一次且不绑定原 Session、
存储失败时保留本标签页内存副本。

`session-operations.browser.js` 用同一静态服务验证原 Session 续作与运维对话框，
覆盖运行禁用、A/B 输入隔离、请求 ID、停止、关闭 / 重新打开、删除确认和备份失败。
同样只使用合成 API，不操作用户的原生 Session。

```bash
playwright-cli run-code --filename=tests/session-operations.browser.js
python3 -m unittest test_workflow_sessions -q
```

# MVP-1.0.0 Team API

纠错回归：`python3 -m unittest discover -s tests -p 'test_recovery.py' -v`。
覆盖真实状态机中的审查退回、同工作区复审、次数上限、停止竞争、丢回执、服务重启、
明确输入阻塞和旧运行不自动复活。原生请求使用确定性替身。
`test_mvp.py` 另外验证恢复 API 的权限 / 幂等以及 Worker 到原生 Coordinator 的参数、上下文和路由。
`mvp-recovery.browser.js` 使用临时 8921 fixture；诊断响应用 UI 替身，手动任务提交使用真实测试 HTTP / SQLite。
真实 ACP 实验与这些自动检查分开记录，见 Release。

`python3 -m unittest discover -s tests -p 'test_mvp.py' -v` 使用临时 SQLite、
临时测试用户与原生接口替身，不调用真实 Coding Agent。
`mvp_browser_fixture.py` + `mvp-team.browser.js` 验证 Web / HTTP 权限与操作；
fixture 不启动 Engine。真实 ACP 运行另见 `docs/MVP-1.0.0-RELEASE.md`。

在新的 fixture 临时数据库上，先运行 `mvp-team.browser.js`，再运行
`mvp-intent.browser.js`。后者验证需求匹配、15 个模板、明确指定优先、
任务手动编辑、跨 Session 草稿隔离、刷新恢复、迟到响应、首次用户提交以及移动端布局。

之后可执行 `mvp-recommend.browser.js`，验证模板提示词切换、后台建议的提交 / 查询 /
过期保护 / 采用 / 人工编辑和工作流保存。此脚本仅模拟生成 HTTP 端点，
使用真实测试 API 保存工作流；不调用真实 Worker，不与原生推荐证据混算。

`python3 -m unittest discover -s tests -p 'test_crew_entry.py' -v` 验证浏览器入口的用户 /
Team 权限、撤权与关闭竞态、父会话绑定、Worker 边界、原生认证复用及不持久化登录链接。
`mvp-crew-entry.browser.js` 复用同一临时 fixture，模拟 Worker 入口和原生页面，
验证新标签页、Session 切换、推荐 Session、连接失败、弹窗拦截及窄屏。
真实原生跳转证据单独保存在本机忽略目录 `evidence/mvp-1.0.0/native-crew-entry.json`，
只记录身份匹配和认证检查布尔值，不保存登录 URL、Cookie 或 Token。
