"""Small, inspectable task contract. Agents, not this file, implement the app."""

STAGES = [
    {"id": "plan", "title": "任务分析", "tool": "Kiro CLI", "backend": "kiro"},
    {"id": "code", "title": "核心编码", "tool": "Claude Code", "backend": "claude"},
    {"id": "review", "title": "安全与测试", "tool": "Codex", "backend": "codex"},
    {"id": "deliver", "title": "前端交付", "tool": "OpenCode", "backend": "opencode"},
]
for stage in STAGES:
    stage["agent"] = f"poc-pipeline-{stage['id']}"

COORDINATOR_PROMPT = """
你是 KiroCrew App 的 Pipeline 协调者。用户要求四种 Coding Tool 真实协作。
先接受用户的自然语言需求。会话背景中会提供本次运行目录和 app-prompt.txt；
如本次尚未登记需求，按背景指示保存用户需求并执行 capture，再进入规划。
plan.md、SPEC.md 和验收测试由 Kiro 规划工作者生成，绝不能要求用户手写这些文件。
任务书 app-prompt.txt 和每阶段 tasks/*.txt 是流程契约，REQUEST.md 是用户原始需求。
按 plan → code → review → deliver 顺序用原生 spawn_run 分派，必须使用任务书指定的
agent、完整 stage 标记、cwd、keep=true、solo_reason=user_requested，
solo_details="用户明确要求 Kiro CLI 规划、Claude Code 编码、Codex 安全测试、OpenCode 前端交付"。
使用 include_memory=false。不自行执行外部 Coding CLI，不代写交付物。
每次分派后结束当前 turn，等待原生子任务完成消息；不要反复轮询或睡眠。
收到完成消息后调用任务书指定的 pipeline.py checkpoint，传入真实 task-id。
spawn_continue 会返回新的 task-id；验收必须使用本次续接的新 ID，不能使用最初的 conversation ID。
只有 checkpoint exit 0 才允许下一阶段。失败要如实报告，并使用原生 spawn_continue
让对应工作者补齐（同样传入 stage 标记），至多补齐两次；不得跳过门槛。
例外是审查发现代码缺陷：先把 review.md 和新增回归测试交回原 Claude 编码会话
spawn_continue 修复（STAGE:code），禁止修改规格或任何测试；重新通过 code checkpoint，
再用原 Codex 审查会话 spawn_continue 复审新候选（STAGE:review）。
每次使用续接返回的新 task-id，最多两轮修复；不让审查者代改核心，不忽略失败测试。
checkpoint 不调用 LLM，只校验原生任务归属、实际后端、文件、diff、测试和哈希。
最终将各阶段任务 ID、验收结果和 report.md 路径返回本 App 父会话。
"""
WORKER_PROMPTS = {
    "plan": "你是规划工作者。按本次任务书工作。需求驱动的任务需要你生成 SPEC.md、plan.md 和 tests/acceptance.test.mjs；旧固定演示已有验收测试时不要修改它。不写实现，不派生任务。",
    "code": "你是编码工作者。按本次 SPEC.md（若存在）、任务书和 plan.md 实现 web/core.mjs。导出接口由本次规格决定，不限于旧演示。不修改基线、验收测试或任务书，不派生任务。",
    "review": "你是审查工作者。读取真实 diff、候选版本与测试证据，运行测试并做安全审查；不修改被审查核心与固定测试。按任务写 review.json 和 review.md，不派生任务。",
    "deliver": "你是前端交付工作者。实际写出 HTML/CSS/JS，导入已经审查的 core.mjs。不得修改核心与固定测试，不安装依赖、不派生任务。完成后运行测试并写 delivery.md，不要只返回开场白。",
}

WEB_SCOPE = """本次 PoC 支持零依赖的本地 Web 小应用：纯 HTML/CSS/ES modules，
业务逻辑放在 web/core.mjs，界面放在 web/index.html、web/style.css、web/ui.mjs。
使用 Node 内置 node:test 验证核心逻辑；通过本地 HTTP 服务打开网页。
不安装依赖、不访问外网、不发布或提交、不访问其他项目或凭据。
需要数据库、登录、远程 API 或部署的需求，请先在 App 说明范围不支持并等待用户调整，
不得默默改成假数据后宣称实现了原需求。"""

EMPTY_CORE = "// Business logic will be implemented by Claude Code after the Kiro planning gate.\n"


def requested_tasks(run):
    """Generic role instructions; the planner chooses the product's real API."""
    common = (
        f"唯一工作目录 {run / 'workspace'}。先阅读 REQUEST.md 与 TASK.md。\n"
        f"{WEB_SCOPE}\n所有 workspace/ 路径均相对于 {run}。"
        "不修改 REQUEST.md、TASK.md、baseline、manifest 或 gates。不要派生子任务。\n"
    )
    return {
        "plan": common + (
            "根据本次用户需求写 SPEC.md：产品行为、边界、核心导出函数及精确输入输出、UI 交互与验收标准。"
            "写 plan.md：模块分工、实施步骤、安全风险、测试策略和前端交付要求。"
            "编写 tests/acceptance.test.mjs，使用 node:test 与 node:assert/strict，"
            "从 ../web/core.mjs 导入你在 SPEC.md 定义的真实函数。至少包含3项有断言的独立测试，"
            "覆盖用户主要功能与异常/边界输入，不得使用 skip/todo 或恒真断言。"
            "此时 core.mjs 是空基线，测试应失败；只生成规格和测试，不写核心或界面。"
            "按用户需求设计，不套用发布说明工作台的函数和交互。"
        ),
        "code": common + (
            "阅读 SPEC.md、plan.md、tests/acceptance.test.mjs。"
            "实现 web/core.mjs 中本次规格定义的导出函数。接口取决于本次需求。"
            "不修改 SPEC.md、plan.md 和验收测试。"
            "运行 node --test tests/*.test.mjs，报告真实结果。"
        ),
        "review": common + (
            f"阅读 SPEC.md、plan.md、{run / 'candidate.diff'}、{run / 'gates/code.json'}、"
            f"{run / 'code-tests.txt'} 和 web/core.mjs。"
            "检查测试是否真实覆盖用户需求、核心实现与规格是否一致、输入校验与注入风险。"
            "运行 node --test tests/*.test.mjs。允许新增 tests/review.test.mjs；"
            "不得修改核心、SPEC.md、plan.md 和验收测试。"
            '写 review.json，格式为 {"verdict":"pass 或 fail","candidate_sha256":"code gate 的真实候选哈希",'
            '"tests_passed":true,"findings":[{"severity":"critical/high/medium/low/info","description":"说明"}],'
            '"summary":"实际审查范围和结论"}。关键需求未覆盖、critical/high 风险或测试失败必须 fail。'
            "另写 review.md 供阅读；不要宣称审查了尚未生成的 UI。"
        ),
        "deliver": common + (
            "阅读 SPEC.md、plan.md、review.json 和 review.md。审查通过后，"
            "实际写 web/index.html、web/style.css、web/ui.mjs，导入 ./core.mjs，"
            "实现本次用户需求中的界面与交互。禁止修改核心、规格、计划和测试。"
            "中文界面，清晰简洁，适合客户演示和手机；不使用 CDN。"
            "HTML 用 script type=module src=./ui.mjs，不可信内容用 textContent。"
            "运行 node --test tests/*.test.mjs，写 delivery.md：使用方式、文件和真实验证范围。"
            "必须落地文件，不能只返回开场白。页面需要本地 HTTP 服务，不能声称直接双击 file:// 即可运行。"
        ),
    }

CONTRACT = """# 发布说明工作台 / Release Notes Studio

零依赖、浏览器本地运行，不安装包、不请求外网。页面使用 ES modules，
通过本 PoC 的本地 HTTP 服务预览；不要声称直接双击 file:// 文件即可运行。

核心模块 web/core.mjs 必须导出：
- parseNotes(text)：按行解析。去掉空白和空行。`feat: 内容`、`fix: 内容`、`docs: 内容`
  识别为同名 category（前缀大小写不敏感），未知前缀/普通文本归为 other，
  未知前缀保留在 text 中。空内容丢弃。
  按 category + text 精确去重，保留首次出现顺序。返回 [{category, text}]。
- escapeHtml(text)：转义 & < > " ' 五个字符。
- renderNotes(items)：返回安全 HTML 字符串，使用 ul/li；
  category 和 text 都必须转义。不得使用 eval 或加载外部内容。

前端允许输入多行变更，点击“生成发布说明”，展示总条目数、分类数量和安全预览，
可复制纯文本结果，提供“载入示例”和“清空”。初始载入示例。
页面中文为主，白底、蓝色强调，清晰排版，适合投屏和手机阅读。
示例输入：
feat: 新增工作区切换
fix: 修复会话状态丢失
docs: 更新快速入门
feat: 新增工作区切换

验收：node --test tests/*.test.mjs。核心固定测试不可修改。
Codex 审查候选核心和实际 diff；OpenCode 在此核心上完成 Web UI，
Web UI 之后仍需独立浏览器验证，不宣称 Codex 已审查尚未生成的 UI。
"""

BASELINE = """export function parseNotes(text) { throw new Error('Not implemented'); }
export function escapeHtml(text) { throw new Error('Not implemented'); }
export function renderNotes(items) { throw new Error('Not implemented'); }
"""

TESTS = r"""import test from 'node:test';
import assert from 'node:assert/strict';
import {parseNotes, escapeHtml, renderNotes} from '../web/core.mjs';

test('blank input', () => assert.deepEqual(parseNotes(' \n \n'), []));
test('categories, whitespace, case and stable deduplication', () => {
  assert.deepEqual(parseNotes(' Feat: Launch \nfix: Bug\nfeat: Launch\nDOCS: Guide\nfix: Launch'),
    [{category:'feat', text:'Launch'}, {category:'fix', text:'Bug'},
     {category:'docs', text:'Guide'}, {category:'fix', text:'Launch'}]);
});
test('unknown categories keep their text; empty entries discarded', () => {
  assert.deepEqual(parseNotes('chore: Tools\nplain text\nfeat:   \nfix:'),
    [{category:'other', text:'chore: Tools'}, {category:'other', text:'plain text'}]);
});
test('CRLF and unicode', () => {
  assert.deepEqual(parseNotes('feat: 中文 🚀\r\nfix: 修复'),
    [{category:'feat',text:'中文 🚀'}, {category:'fix',text:'修复'}]);
});
test('all five HTML characters escaped', () => {
  assert.equal(escapeHtml('&<>"\''), '&amp;&lt;&gt;&quot;&#39;');
});
test('content cannot create executable HTML', () => {
  const html = renderNotes(parseNotes('feat: <img src=x onerror="alert(1)">'));
  assert.match(html, /<ul\b/);
  assert.match(html, /<li\b/);
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img'));
});
test('category cannot inject attributes or elements', () => {
  const html = renderNotes([{category:'"><script>bad()</script>',text:'safe'}]);
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;script&gt;'));
});
test('empty render is a valid list', () => {
  assert.match(renderNotes([]), /<ul\b[^>]*>\s*<\/ul>/);
});
"""
