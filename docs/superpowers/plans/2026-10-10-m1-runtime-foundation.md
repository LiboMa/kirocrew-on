# M1 运行底座 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把新旧两版代码等价迁入 `kirocrew-on`，在测试保护下完成配置解耦、组合启动、schema 迁移、按 Session 并发的 Engine、无桌面服务与部署材料；先在本机 macOS 用真实 ACP 跑通完整链路，再在 EC2（AL2023 x86_64）上完成真实最小研发与返工验收。

**Architecture:** Part A 全部在本机完成且不依赖外部输入：第 0 步原样迁入并建立回归基线，之后每个任务只做一类改动并保持全量回归绿色。Part A′ 在本机用独立端口与数据目录拉起三服务，以真实 ACP 验收。Part B 是 EC2 上的操作性任务，每个任务开始前都有授权关口。

**Tech Stack:** Python 3.12+（标准库 + PyYAML；`tomllib`、`sqlite3`、`concurrent.futures`），原生 JS（无构建），Node ≥22（仅 ACP 适配器与 JS 检查），bash，systemd，CloudFormation。

**Spec:** `docs/superpowers/specs/2026-10-10-kirocrew-on-design.md`（§4 模块地图、§8.2 运行可靠性、§12 运行时与部署、§14 数据与迁移、§16 M1）；验收条件见 `docs/MVP-RELEASE.md` §5。

## Global Constraints

- 迁入来源：`/Users/malibo/MyDev/Kiro-Family-Spaces/kirocrew-octopus`（工作树，含未提交文件）；交叉核对 `/Users/malibo/MyDev/Kiro-Family-Spaces/experiments/kirocrew-app-poc`。**两个来源目录只读，不修改、不 reset、不 clean。**
- 不读取、不复制、不打印 `~/.kiro`、`state/`、`evidence/`、`backups/` 中的任何运行数据与凭据；测试只使用临时目录。
- Python ≥ 3.12；`octopus/` 运行时依赖只允许标准库与 PyYAML（`requirements-workflow.txt`：`PyYAML>=6.0,<7`）。
- 工具范围：Kiro CLI、Claude Code、Codex、OpenCode；不引入 Gemini CLI。
- 不绕过 KiroCrew 直接启动 Coding CLI；不重新实现 ACP；不关闭 KiroCrew 沙箱；不默认全部自动批准。
- 代码注释沿用现有风格（英文、简短）；面向用户的错误信息用中文。
- 迁入的测试只允许修改 import / 路径，不修改断言语义。
- **提交规则（负责人 CLAUDE.md + 2026-10-11 授权）：Part A / A′ 每个任务的测试与全量回归全绿后直接本地提交；Part A 结束后统一汇报并做一次全分支独立审查；任何情况下不 push。**
- 回归入口统一为 `tools/regress.sh`；每个任务结束时必须全绿。

## Review Focus

1. **systemd 下的相对路径**：配置文件中的相对路径必须相对配置文件所在目录解析，而不是进程 cwd（Task 4 `test_file_relative_paths_resolve_against_file_directory`）。
2. **组合静默丢失**：普通 Gateway 启动或 re-exec 后 `PocProviderRegistry` 不在位时，不能表现为正常；每次路由必须落路由日志，就绪检查必须核对组合标记（Task 5 `test_every_template_reaches_its_backend_and_is_logged`、Task 9 `test_runtime_marker_requires_composition`）。
3. **旧库升级**：用 MVP 1.0.0 的原始 SCHEMA 创建且已有数据的数据库，升级后数据不丢、版本正确、迁移前有备份（Task 6 `test_legacy_mvp_database_keeps_data`）。
4. **隧道 Host**：经 SSM 转发到 `localhost:18918` 的请求，未配置时被拒绝、配置后放行；Origin 不一致仍拒绝写入（Task 8）。
5. **SIGTERM 中途停止**：服务收到 SIGTERM 时 HTTP 停止接收、Engine 在有限时间内收尾、进程以 0 退出；重启后 Run 继续推进（Task 7 `test_drain_waits_for_inflight_advance`、Task 9 `test_serve_exits_cleanly_on_sigterm`）。

---

## 文件结构

| 路径 | 职责 | 任务 |
|---|---|---|
| `tools/import_baseline.py` | 一次性等价迁入与 PROVENANCE 生成 | 1 |
| `tools/regress.sh` | 离线全量回归入口（CI 与本机共用） | 1 |
| `docs/PROVENANCE.md`、`docs/inherited/` | 迁入来源记录、继承的契约文档 | 1 |
| `docs/handover/*.md` | 功能对照表、事实清单、调用链、已知故障 | 2 |
| `octopus/gateway_client.py`、`sources.py`、`documents.py`、`prompts.py`、`templates/` | 从根目录迁入平台包的模块 | 3 |
| 根目录 `api.py`、`workflow_workspace.py`、`workflow_files.py`、`development_prompts.py` | 兼容别名（`sys.modules` 指向新模块） | 3 |
| `octopus/config.py` | 统一运行时配置 | 4 |
| `octopus/__main__.py` | CLI：读取配置、服务入口、运维命令 | 4、9、10 |
| `composition/registry.py`、`environment.py`、`templates.py`、`gateway.py` | KiroCrew 组合：路由、环境、Agent 模板、Gateway 入口 | 5 |
| 根目录 `poc.py` | 兼容层，委托 `composition` | 5 |
| `octopus/migrations.py`、`octopus/db.py` | schema 迁移与备份 | 6 |
| `octopus/engine.py` | 按 Session 并发推进、不可用退避、收尾 | 7 |
| `octopus/server.py` | Host 白名单 | 8 |
| `octopus/ops.py` | wait-ready 就绪检查 | 9 |
| `deploy/systemd/*.service` | 服务单元 | 9 |
| `octopus/backup.py` | 一致性备份、归档、独立目录恢复校验 | 10 |
| `deploy/aws/`、`deploy/install/`、`deploy/config/`、`deploy/versions.env` | EC2 部署材料 | 11 |
| `docs/OPERATIONS.md` | 运维手册 | 12 |

---

# Part A：本机代码与部署材料

### Task 1: 等价迁入与回归基线

**Files:**
- Create: `tools/import_baseline.py`、`tools/regress.sh`、`tests/test_import_baseline.py`
- Create（由工具生成）：迁入文件、`docs/PROVENANCE.md`、`docs/inherited/**`
- Modify: `.github/workflows/ci.yml`（迁入后改为调用 `tools/regress.sh`）

**Interfaces:**
- Produces: `tools.import_baseline.run(source: Path, legacy: Path, dest: Path) -> list[dict]`（每项 `{"path", "sha256", "origin", "git_status", "legacy"}`）；`tools/regress.sh` 为后续所有任务的回归入口。

- [ ] **Step 1: 建立虚拟环境**

```bash
cd /Users/malibo/MyDev/Kiro-Family-Spaces/experiments/kirocrew-on
python3.12 -m venv .venv
.venv/bin/python -m pip install 'PyYAML>=6.0,<7'
```

Expected: `Successfully installed PyYAML-6.x`

- [ ] **Step 2: 写迁入工具的失败测试**

`tests/test_import_baseline.py`:

```python
"""Baseline import copies only the manifest, never runtime data, and records provenance."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import import_baseline as importer  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class ImportBaselineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.source, self.legacy, self.dest = root / "source", root / "legacy", root / "dest"
        for directory in (self.source / "octopus" / "__pycache__", self.source / "state", self.legacy, self.dest):
            directory.mkdir(parents=True)
        (self.source / "octopus" / "db.py").write_text("SCHEMA = 1\n")
        (self.source / "octopus" / "__pycache__" / "db.cpython-312.pyc").write_bytes(b"x")
        (self.source / "api.py").write_text("NEW = 1\n")
        (self.legacy / "api.py").write_text("OLD = 1\n")
        (self.source / "poc.py").write_text("same\n")
        (self.legacy / "poc.py").write_text("same\n")
        (self.source / "state" / "secret.token").write_text("never")
        git(self.source, "init", "-q")
        git(self.source, "add", "poc.py")
        git(self.source, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
        (self.source / "poc.py").write_text("same\n")

    def tearDown(self):
        self.temporary.cleanup()

    def test_copies_manifest_files_and_skips_runtime_data(self):
        records = importer.run(self.source, self.legacy, self.dest)
        paths = {r["path"] for r in records}
        self.assertIn("octopus/db.py", paths)
        self.assertIn("api.py", paths)
        self.assertFalse((self.dest / "state").exists())
        self.assertFalse((self.dest / "octopus" / "__pycache__").exists())

    def test_records_hash_git_status_and_legacy_comparison(self):
        records = {r["path"]: r for r in importer.run(self.source, self.legacy, self.dest)}
        self.assertEqual(len(records["api.py"]["sha256"]), 64)
        self.assertEqual(records["api.py"]["legacy"], "differs")
        self.assertEqual(records["poc.py"]["legacy"], "identical")
        self.assertEqual(records["octopus/db.py"]["git_status"], "untracked")
        self.assertEqual(records["poc.py"]["git_status"], "committed")
        text = (self.dest / "docs" / "PROVENANCE.md").read_text()
        self.assertIn(records["api.py"]["sha256"], text)

    def test_refuses_to_overwrite_existing_files(self):
        (self.dest / "api.py").write_text("refactored\n")
        with self.assertRaises(FileExistsError):
            importer.run(self.source, self.legacy, self.dest)
        self.assertEqual((self.dest / "api.py").read_text(), "refactored\n")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行测试确认失败**

Run: `.venv/bin/python -m unittest tests/test_import_baseline.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'import_baseline'`

- [ ] **Step 4: 实现迁入工具**

`tools/import_baseline.py`:

```python
"""Copy the audited baseline from the source trees and record provenance (run once).

python3 tools/import_baseline.py \
  --source /Users/malibo/MyDev/Kiro-Family-Spaces/kirocrew-octopus \
  --legacy /Users/malibo/MyDev/Kiro-Family-Spaces/experiments/kirocrew-app-poc
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
from pathlib import Path
import shutil
import subprocess
import time

CODE = [
    "octopus/*.py", "octopus/ui/*", "octopus_cli.py",
    "api.py", "poc.py", "development_prompts.py",
    "workflow.py", "workflow_files.py", "workflow_library.py", "workflow_sessions.py",
    "workflow_workspace.py", "workflow_launch.py",
    "pipeline.py", "pipeline_spec.py", "pipeline_launch.py",
    "launch.py", "reload_gateway.py", "build_workflow_ui.py", "setup.sh", "start.sh",
    "package.json", "package-lock.json", "requirements-workflow.txt",
    "test_workflow.py", "test_workflow_library.py", "test_workflow_review.py", "test_workflow_sessions.py",
    "test_workflow_workspace.py", "test_pipeline.py", "test_reload_gateway.py", "test_routing.py",
    "tests/*.py", "tests/*.js", "tests/README.md",
    "ui/*.html", "ui/*.css", "ui/*.js", "ui/*.json",
    "workflows/*.json", "examples/*", ".github/workflows/ci.yml",
]
DOCS = [
    "WORKFLOW-MVP.md", "PIPELINE.md",
    "docs/CLAUDE-CODE-HANDOFF-PROMPT.md", "docs/COORDINATOR-RECOVERY.md", "docs/DEVELOPMENT-CONTRACT.md",
    "docs/MVP-1.0.0-API.md", "docs/MVP-1.0.0-PLAN.md", "docs/MVP-1.0.0-RELEASE.md", "docs/MVP-1.0.0-STARTUP.md",
    "docs/SESSION-OPERATIONS.md", "docs/STARTUP.md", "docs/FEATURES-AND-DECISIONS.md",
    "docs/architecture/*.md", "docs/experiments/*.md",
]
# fnmatch's "*" also matches "/": exclude report-only UI subtrees explicitly.
EXCLUDE = ["*/__pycache__/*", "*.pyc", "state/*", "evidence/*", "backups/*", "node_modules/*",
           "ui/enterprise-video/*", "ui/mvp-report/*"]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def matches(relative, patterns):
    return any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)


def selected(source, patterns):
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source).as_posix()
        if path.is_file() and matches(relative, patterns) and not matches(relative, EXCLUDE):
            yield relative


def git_status(source, relative):
    result = subprocess.run(["git", "status", "--porcelain", "--", relative], cwd=source,
                            capture_output=True, text=True)
    if result.returncode != 0:
        return "not-a-repository"
    code = result.stdout[:2]
    return {"": "committed", "??": "untracked", " M": "modified", "M ": "staged", "MM": "modified"}.get(code, code.strip())


def run(source, legacy, dest):
    source, legacy, dest = Path(source).resolve(), Path(legacy).resolve(), Path(dest).resolve()
    plan = [(relative, relative, "code") for relative in selected(source, CODE)]
    plan += [(relative, "docs/inherited/" + relative, "doc") for relative in selected(source, DOCS)]
    clashes = [target for _, target, _ in plan if (dest / target).exists()]
    if clashes:
        raise FileExistsError("目标文件已存在，拒绝覆盖：" + ", ".join(clashes[:5]))
    records = []
    for relative, target, kind in plan:
        origin = source / relative
        (dest / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, dest / target)
        twin = legacy / relative
        records.append({
            "path": target, "origin": relative, "kind": kind, "sha256": sha256(origin),
            "git_status": git_status(source, relative),
            "legacy": "absent" if not twin.is_file() else ("identical" if sha256(twin) == sha256(origin) else "differs"),
        })
    write_provenance(dest, source, legacy, records)
    return records


def write_provenance(dest, source, legacy, records):
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=source, capture_output=True, text=True).stdout.strip()
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=source, capture_output=True, text=True).stdout.strip()
    lines = [
        "# 迁入来源记录（PROVENANCE）", "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S %z')}",
        f"来源：`{source}`（分支 `{branch or '未知'}`，HEAD `{head or '未知'}`，含未提交文件）",
        f"交叉核对：`{legacy}`", "",
        "`git_status` 为文件在来源仓库中的状态；`legacy` 为与旧版运行目录同名文件的比较结果。",
        "本表记录迁入时刻的内容；之后的修改以本仓库 git 历史为准。", "",
        "| 路径 | 来源 | 类型 | 来源 git 状态 | 与旧版运行目录 | sha256 |", "|---|---|---|---|---|---|",
    ]
    lines += [f"| `{r['path']}` | `{r['origin']}` | {r['kind']} | {r['git_status']} | {r['legacy']} | `{r['sha256']}` |"
              for r in records]
    target = dest / "docs" / "PROVENANCE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--legacy", required=True)
    parser.add_argument("--dest", default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    imported = run(args.source, args.legacy, args.dest)
    print(f"迁入 {len(imported)} 个文件，记录见 docs/PROVENANCE.md")
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/bin/python -m unittest tests/test_import_baseline.py -v`
Expected: 3 tests OK

- [ ] **Step 6: 执行迁入**

```bash
.venv/bin/python tools/import_baseline.py \
  --source /Users/malibo/MyDev/Kiro-Family-Spaces/kirocrew-octopus \
  --legacy /Users/malibo/MyDev/Kiro-Family-Spaces/experiments/kirocrew-app-poc
git status --short | head -50
```

Expected: 打印「迁入 N 个文件」；`git status` 只出现 manifest 中的代码、测试、UI、文档与 `docs/PROVENANCE.md`，没有 `state/`、`evidence/`、`__pycache__`、`*.zip`、`*.command`。

- [ ] **Step 7: 写回归入口**

`tools/regress.sh`:

```bash
#!/usr/bin/env bash
# Offline regression: temporary data and native substitutes only; no credentials, no Coding tasks.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
fi
"$PY" -m unittest test_workflow test_workflow_workspace test_workflow_library test_pipeline \
  test_reload_gateway test_workflow_review test_workflow_sessions -q
"$PY" build_workflow_ui.py --check
node --test tests/workflow-editor-store.test.js
for script in ui/workflow.js ui/workflow-progress.js ui/workflow-view.js ui/crew-entry.js ui/live.js octopus/ui/*.js; do
  node --check "$script"
done
"$PY" -m unittest discover -s tests -p 'test_*.py' -q
echo "regress: OK"
```

```bash
chmod +x tools/regress.sh
```

- [ ] **Step 8: 运行全量回归**

Run: `tools/regress.sh`
Expected: 最后一行 `regress: OK`。若某个迁入测试因缺少来源文件失败：把该文件加入 `CODE` 清单、删除已迁入文件后重新执行 Step 6，并在 PROVENANCE 中可见；**不修改测试断言**。把实际测试数量（旧版 unittest 数、`tests/` unittest 数）记入本任务报告。

- [ ] **Step 9: CI 改为调用回归入口**

把 `.github/workflows/ci.yml` 中 `Install workflow parser` 之后的全部步骤替换为：

```yaml
      - name: Install workflow parser
        run: python -m pip install -r requirements-workflow.txt
      - uses: actions/setup-node@39370e3970a6d050c480ffad4ff0ed4d3fdee5af # v4
        with:
          node-version: "22"
      - name: Offline regression
        run: PYTHON=python tools/regress.sh
```

（原 `build_pipeline_kit.py` 步骤删除：该分发脚本未迁入，旧版发布包不再从本仓库构建；在 Task 2 功能对照表中记为“计划淘汰”。）

- [ ] **Step 10: 报告并等待负责人确认后提交**

向负责人报告：迁入文件数、两类测试实际数量与结果、`legacy=differs` 的文件清单。确认后：

```bash
git add tools/ tests/test_import_baseline.py docs/PROVENANCE.md docs/inherited .github \
  octopus octopus_cli.py *.py *.sh package.json package-lock.json requirements-workflow.txt tests ui workflows examples
git commit -m "chore: 等价迁入 kirocrew-octopus 新旧两版代码并建立离线回归基线"
```

---

### Task 2: 接手材料

**Files:**
- Create: `docs/handover/FEATURE-MATRIX.md`、`docs/handover/FACTS.md`、`docs/handover/CALL-CHAINS.md`、`docs/handover/KNOWN-ISSUES.md`、`docs/handover/GENERATION-REFERENCE.md`

**Interfaces:**
- Consumes: Task 1 迁入的代码与 `docs/inherited/`。
- Produces: 后续任务与 M2 / M3 规划引用的事实基线。

- [ ] **Step 1: 功能对照表**

`docs/handover/FEATURE-MATRIX.md`：一张表，每行一个功能，列为「功能 / 旧版（8917/8916）实现位置 / 新版（8918）实现位置 / 状态 / kirocrew-on 归属里程碑 / 备注」。状态只用：`已复用`、`部分实现`、`缺失`、`计划淘汰及替代`。至少覆盖：
Web 引导构建；JSON / YAML / Markdown 导入导出；工作流版本库；Session 列表与搜索；1–8 阶段与阶段级工具 / 模型 / Effort；意图生成与采纳；15 个模板（新版 `octopus/intent.py`，旧版 12 个）；固定四阶段 Pipeline 与确定性门禁（`pipeline.py checkpoint`）；规划锁定哈希；源码准备（`workflow_workspace.prepare_project`）；原 Session 续作；阶段重试；停止 / 关闭 / 重开 / 删除 / 导出；Session ZIP 文档（开发手册、Release Notes）；原生入口（crew-entry）；用户 / Token / Team / 共享；SSE；Coordinator 纠错；MCP 13 个工具；Python client；`build_pipeline_kit.py` 分发包（计划淘汰）；`*.command` 与 `入口.html`（计划淘汰，由 systemd 与控制台替代）。
每行的实现位置写到 `文件:函数`。

- [ ] **Step 2: 事实清单**

`docs/handover/FACTS.md`：逐条列出可核实的当前事实，每条附来源（文件:行 或命令输出），至少包括：来源仓库分支与 HEAD；未提交文件数；本机 KiroCrew 版本 0.8.0、`poc.SUPPORTED_VERSIONS`；ACP 适配器版本（`package-lock.json`：claude-agent-acp 0.84.0、codex-acp 1.11.0）；端口 5476 / 8917 / 8916 / 8918 / 8928；新版 SQLite 表清单与 `schema_version`=1 且无迁移机制；Engine 单线程串行；Worker 与 Gateway 必须同机（`api.py` 写死 `127.0.0.1`）；原生 token 每次调用现取；crew-entry token 为整个 Gateway 的 8h owner 登录；Task 1 实测的测试数量。

- [ ] **Step 3: 调用链**

`docs/handover/CALL-CHAINS.md`：用编号步骤（带 `文件:函数`）写出四条链：
1. 新版提交：`POST /v1/runs` → `domain.Service.start` → Engine `tick/advance` → Worker `/prepare` → `/dispatch` → 原生 `/api/spawn` → `/inspect` → 验收 → `recovery.automatic`。
2. 新版纠错：`recovery.schedule` → `Coordinator.advance` → `decision.json` → `finish`。
3. 旧版 Workflow：`/api/start` → `prepare/submit` → 父 Agent `workflow.py next` → `spawn_run` → `accept` → observer。
4. 旧版 Pipeline：`/intake` → `new_run/submit_run` → 父 Agent → `pipeline.py checkpoint`（门禁）→ `report`。

- [ ] **Step 4: 已知故障清单**

`docs/handover/KNOWN-ISSUES.md`：复制 `docs/MVP-RELEASE.md` §9 的 K01–K15，每条补「复现方式 / 现有缓解 / 本仓库处理任务」，再追加 Task 1 中发现的新问题（没有则写“Task 1 未发现新问题”）。

- [ ] **Step 5: 旧版自动生成参考（M2 Pipeline 生成的设计输入）**

`docs/handover/GENERATION-REFERENCE.md`：以 `/Users/malibo/MyDev/Kiro-Family-Spaces/experiments/kirocrew-app-poc` 为准（与迁入代码逐文件对照，差异注明），分析旧版“根据需求自动生成 / 编排”的实现，逐项写出 `文件:函数`、输入、输出、校验与可借鉴点：
1. 意图生成：`workflow_library.start_design` / `_generate` / `design_status` 与 `development_prompts.intent_prompt`——生成任务如何组织、使用哪个 Agent、产出格式（单个 JSON/YAML 代码块）、校验与采纳流程、迟到结果与草稿隔离。
2. Pipeline 规划阶段：`pipeline_spec.STAGES` / `COORDINATOR_PROMPT` / `WORKER_PROMPTS` / `requested_tasks`，`pipeline.capture_request` / `new_run`——REQUEST.md、TASK.md、`tasks/*.txt` 的生成，Kiro 产出 SPEC.md、plan.md、验收测试，以及规划后锁定哈希。
3. 确定性门禁：`pipeline.checkpoint` / `tests` / `validate_review` 的判定条件与证据文件（`gates/<stage>.json`、`gate-history.jsonl`）。
4. 模板：`ui/workflow-templates.json` 的 12 个模板结构（阶段、角色、提示词、验收契约），与新版 `octopus/intent.py` 的 3 个 CORE 模板对照。
5. 生成样例：`examples/generated-review-workflow.{md,yaml}`；旧版运行目录 `state/workflow-designs/` 只统计数量与结构字段，**不复制、不引用其中的需求原文**。
6. 结论：哪些逻辑直接复用到 M2 `octopus/pipelines.py` / `gates.py` / `designs.py`，哪些因“Engine 是唯一派发者”而改写（旧版由父 Agent 按 `next / accept` 推进），哪些淘汰。

- [ ] **Step 6: 运行回归并提交**

Run: `tools/regress.sh` → `regress: OK`

```bash
git add docs/handover
git commit -m "docs: 接手材料——功能对照表、事实清单、调用链、已知故障与旧版生成参考"
```

---

### Task 3: 平台包边界——迁入根模块并保留兼容别名

**Files:**
- Move: `api.py` → `octopus/gateway_client.py`；`workflow_workspace.py` → `octopus/sources.py`；`workflow_files.py` → `octopus/documents.py`；`development_prompts.py` → `octopus/prompts.py`
- Create: 根目录同名兼容别名四个；`octopus/templates/workflow-templates.json`；`tests/test_layout.py`
- Modify: `octopus/worker.py:16-17`、`octopus/domain.py:272`、`octopus/designs.py:34,63`、`octopus/intent.py:27`、`octopus/documents.py`（`yaml_module`）

**Interfaces:**
- Consumes: Task 1 基线。
- Produces: `octopus.gateway_client`（原 `api`：`token`、`request`、`chat_entry_url`、`export_session`、`GatewayError`、`ReadOnlyGateway`）、`octopus.sources`（原 `workflow_workspace`）、`octopus.documents`（原 `workflow_files`）、`octopus.prompts`（原 `development_prompts`）、`octopus.intent.LEGACY`（模板 JSON 路径）。

- [ ] **Step 1: 写失败测试**

`tests/test_layout.py`:

```python
"""Platform package boundaries: octopus/ never imports legacy root modules."""
import ast
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
LEGACY = {"api", "workflow_workspace", "workflow_files", "development_prompts", "poc", "workflow",
          "workflow_library", "workflow_sessions", "pipeline", "pipeline_spec"}


def imported(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0]


class LayoutTests(unittest.TestCase):
    def test_platform_package_does_not_import_legacy_root_modules(self):
        offenders = {str(p.relative_to(ROOT)): sorted(set(imported(p)) & LEGACY) for p in (ROOT / "octopus").rglob("*.py")}
        self.assertEqual({k: v for k, v in offenders.items() if v}, {})

    def test_legacy_names_are_the_same_module_objects(self):
        import api, workflow_workspace, workflow_files, development_prompts
        from octopus import gateway_client, sources, documents, prompts
        self.assertIs(api, gateway_client)
        self.assertIs(workflow_workspace, sources)
        self.assertIs(workflow_files, documents)
        self.assertIs(development_prompts, prompts)

    def test_templates_live_in_the_platform_package_and_legacy_copy_matches(self):
        from octopus import intent
        self.assertEqual(intent.LEGACY, ROOT / "octopus" / "templates" / "workflow-templates.json")
        self.assertEqual((ROOT / "ui" / "workflow-templates.json").read_bytes(), intent.LEGACY.read_bytes())

    def test_yaml_requires_installed_pyyaml_without_mac_fallback(self):
        from octopus import documents
        with patch.dict(sys.modules, {"yaml": None}):
            with self.assertRaisesRegex(ValueError, "PyYAML"):
                documents.yaml_module()
        self.assertNotIn("/Applications", (ROOT / "octopus" / "documents.py").read_text())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m unittest tests/test_layout.py -v`
Expected: FAIL（`ImportError: cannot import name 'gateway_client'` 等）

- [ ] **Step 3: 移动模块并建立别名**

```bash
git mv api.py octopus/gateway_client.py
git mv workflow_workspace.py octopus/sources.py
git mv workflow_files.py octopus/documents.py
git mv development_prompts.py octopus/prompts.py
mkdir -p octopus/templates && cp ui/workflow-templates.json octopus/templates/workflow-templates.json
```

为每个旧名创建别名文件（以 `api.py` 为例，其余三个只替换模块名与说明）：

`api.py`:

```python
"""Compatibility alias: this module moved to octopus.gateway_client."""
import sys

from octopus import gateway_client as _module

sys.modules[__name__] = _module
```

`workflow_workspace.py` → `from octopus import sources as _module`；`workflow_files.py` → `from octopus import documents as _module`；`development_prompts.py` → `from octopus import prompts as _module`。

- [ ] **Step 4: 改平台包内的 import**

- `octopus/worker.py` 第 16–17 行改为：

```python
from . import gateway_client as crew_api
from . import sources as workspace
```

- `octopus/domain.py` 第 272 行改为 `from .sources import normalize_source`
- `octopus/designs.py` 第 34 行改为 `from .documents import parse_document`；第 63 行改为 `from .prompts import intent_prompt`
- `octopus/intent.py` 第 27 行改为：

```python
LEGACY = Path(__file__).resolve().parent / "templates" / "workflow-templates.json"
```

- `octopus/documents.py` 的 `yaml_module` 整体替换为：

```python
def yaml_module():
    try:
        import yaml
    except ImportError:
        raise ValueError("YAML 需要 PyYAML；请安装 requirements-workflow.txt 中的依赖。") from None
    return yaml
```

并删除不再使用的 `importlib.util` import（若 `sys` 仍被使用则保留）。

- [ ] **Step 5: 运行新测试与全量回归**

Run: `.venv/bin/python -m unittest tests/test_layout.py -v && tools/regress.sh`
Expected: 4 tests OK；`regress: OK`（旧版测试经别名导入，`patch("api.token")` 作用于同一模块对象）

- [ ] **Step 6: 报告并提交（确认后）**

```bash
git add -A octopus api.py workflow_workspace.py workflow_files.py development_prompts.py tests/test_layout.py
git commit -m "refactor: 平台包不再依赖根目录旧模块，旧名保留为模块别名"
```

---

### Task 4: 统一运行时配置

**Files:**
- Create: `octopus/config.py`、`tests/test_config.py`、`tests/test_cli.py`
- Modify: `octopus/__main__.py`（全文重写为 `build_parser(settings)` + `main(argv)`）

**Interfaces:**
- Produces:
  - `octopus.config.Settings`（frozen dataclass）字段：`data_dir: Path`、`worker_data_dir: Path`、`worker_token_file: Path`、`crew_home: Path`、`kiro_home: Path`、`routing_log: Path`、`log_dir: Path`、`gateway_runtime_file: Path`、`api_host: str`、`api_port: int`、`allowed_hosts: tuple[str, ...]`、`public_url: str`、`engine_workers: int`、`retry_unavailable: float`、`worker_host: str`、`worker_port: int`、`worker_isolation: str`、`gateway_port: int`、`crew_url: str`、`kirocrew_python: Path | None`、`adapters_dir: Path | None`、`path_prepend: tuple[Path, ...]`、`tools: dict[str, Path]`
  - `octopus.config.load(path=None, *, environ=None, overrides=None, cwd=None) -> Settings`
  - `octopus.config.ConfigError(ValueError)`
  - `octopus.__main__.build_parser(settings, environ=None) -> argparse.ArgumentParser`（`environ` 仅用于读取 `OCTOPUS_URL`，默认 `os.environ`）、`octopus.__main__.settings_from_argv(argv, environ=None) -> (Settings, list[str])`、`octopus.__main__.main(argv=None)`

- [ ] **Step 1: 写配置的失败测试**

`tests/test_config.py`:

```python
"""Settings precedence: defaults < TOML file < OCTOPUS_* environment < explicit overrides."""
from pathlib import Path
import tempfile
import unittest

from octopus.config import ConfigError, load


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.cwd = self.root / "cwd"
        self.cwd.mkdir()
        self.env = {"HOME": str(self.home)}

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, text):
        path = self.root / "etc" / "octopus.toml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        return path

    def test_defaults_are_relative_to_cwd_and_home(self):
        s = load(environ=self.env, cwd=self.cwd)
        self.assertEqual(s.data_dir, self.cwd / "state/platform")
        self.assertEqual(s.worker_data_dir, self.cwd / "state/worker")
        self.assertEqual(s.crew_home, self.home / ".kiro/crew")
        self.assertEqual(s.kiro_home, self.home / ".kiro")
        self.assertEqual((s.api_port, s.worker_port, s.gateway_port), (8918, 8928, 5476))
        self.assertEqual(s.engine_workers, 4)

    def test_file_relative_paths_resolve_against_file_directory(self):
        path = self.write('data_dir = "data/platform"\nworker_data_dir = "/srv/worker"\n')
        s = load(path, environ=self.env, cwd=self.cwd)
        self.assertEqual(s.data_dir, path.parent / "data/platform")
        self.assertEqual(s.worker_data_dir, Path("/srv/worker"))

    def test_environment_overrides_file_and_resolves_against_cwd(self):
        path = self.write('data_dir = "data/platform"\napi_port = 9000\n')
        env = {**self.env, "OCTOPUS_DATA_DIR": "env/platform", "OCTOPUS_ALLOWED_HOSTS": "localhost:18918, ops.example:443"}
        s = load(path, environ=env, cwd=self.cwd)
        self.assertEqual(s.data_dir, self.cwd / "env/platform")
        self.assertEqual(s.api_port, 9000)
        self.assertEqual(s.allowed_hosts, ("localhost:18918", "ops.example:443"))

    def test_overrides_win_and_none_is_ignored(self):
        s = load(environ={**self.env, "OCTOPUS_API_PORT": "9001"}, overrides={"api_port": 9002, "data_dir": None},
                 cwd=self.cwd)
        self.assertEqual(s.api_port, 9002)
        self.assertEqual(s.data_dir, self.cwd / "state/platform")

    def test_config_file_from_environment(self):
        path = self.write('worker_port = 9100\n')
        s = load(environ={**self.env, "OCTOPUS_CONFIG": str(path)}, cwd=self.cwd)
        self.assertEqual(s.worker_port, 9100)

    def test_tools_section_and_environment(self):
        path = self.write('[tools]\nclaude = "/opt/tools/claude"\n')
        s = load(path, environ={**self.env, "OCTOPUS_TOOL_CODEX": "/opt/tools/codex"}, cwd=self.cwd)
        self.assertEqual(s.tools, {"claude": Path("/opt/tools/claude"), "codex": Path("/opt/tools/codex")})

    def test_invalid_values_are_rejected(self):
        for text in ('api_port = 0\n', 'unknown_key = 1\n', 'worker_isolation = "shared"\n',
                     'engine_workers = 99\n', '[tools]\ngemini = "/x"\n', 'allowed_hosts = ["bad host"]\n'):
            with self.subTest(text=text), self.assertRaises(ConfigError):
                load(self.write(text), environ=self.env, cwd=self.cwd)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m unittest tests/test_config.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'octopus.config'`

- [ ] **Step 3: 实现配置模块**

`octopus/config.py`:

```python
"""Single source of runtime settings: defaults < TOML file < OCTOPUS_* environment < explicit overrides.

Relative paths resolve against the directory of the layer that supplied them:
the config file's directory for file values, the process cwd for defaults,
environment variables and overrides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import sys
import tomllib

TOOLS = ("kiro_cli", "claude", "codex", "opencode", "node")
ISOLATION = ("trusted-local", "dedicated")
MAC_KIROCREW_PYTHON = Path("/Applications/KiroCrew.app/Contents/Resources/backend-dist/"
                           "kirocrew-backend-arm64/bin/python3.12")
PATHS = {"data_dir", "worker_data_dir", "worker_token_file", "crew_home", "kiro_home", "routing_log",
         "log_dir", "gateway_runtime_file", "kirocrew_python", "adapters_dir"}
INTS = {"api_port": (1, 65535), "worker_port": (1, 65535), "gateway_port": (1, 65535), "engine_workers": (1, 32)}
FLOATS = {"retry_unavailable": (0.0, 600.0)}
STRINGS = {"api_host", "worker_host", "public_url", "crew_url", "worker_isolation"}
HOST = re.compile(r"^[A-Za-z0-9.\-]+(:\d{1,5})?$|^\[[0-9A-Fa-f:]+\](:\d{1,5})?$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    worker_data_dir: Path
    worker_token_file: Path
    crew_home: Path
    kiro_home: Path
    routing_log: Path
    log_dir: Path
    gateway_runtime_file: Path
    api_host: str = "127.0.0.1"
    api_port: int = 8918
    allowed_hosts: tuple[str, ...] = ()
    public_url: str = ""
    engine_workers: int = 4
    retry_unavailable: float = 2.0
    worker_host: str = "127.0.0.1"
    worker_port: int = 8928
    worker_isolation: str = "trusted-local"
    gateway_port: int = 5476
    crew_url: str = ""
    kirocrew_python: Path | None = None
    adapters_dir: Path | None = None
    path_prepend: tuple[Path, ...] = ()
    tools: dict = field(default_factory=dict)


def _path(value, base):
    path = Path(os.path.expandvars(str(value))).expanduser()
    return path if path.is_absolute() else base / path


def _layer(values, base):
    """Validate one layer's raw values and resolve its relative paths against ``base``."""
    known = PATHS | set(INTS) | set(FLOATS) | STRINGS | {"allowed_hosts", "path_prepend", "tools"}
    unknown = set(values) - known
    if unknown:
        raise ConfigError("未知配置项：" + ", ".join(sorted(unknown)))
    result = {}
    for key, value in values.items():
        if value is None:
            continue
        if key in PATHS:
            result[key] = _path(value, base)
        elif key in INTS:
            low, high = INTS[key]
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise ConfigError(f"{key} 必须为整数") from None
            if not low <= number <= high:
                raise ConfigError(f"{key} 须在 {low}–{high} 之间")
            result[key] = number
        elif key in FLOATS:
            low, high = FLOATS[key]
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ConfigError(f"{key} 必须为数字") from None
            if not low <= number <= high:
                raise ConfigError(f"{key} 须在 {low}–{high} 之间")
            result[key] = number
        elif key == "allowed_hosts":
            hosts = tuple(h.strip() for h in (value.split(",") if isinstance(value, str) else value) if h.strip())
            if not all(HOST.match(h) for h in hosts):
                raise ConfigError("allowed_hosts 只能包含 host[:port]")
            result[key] = hosts
        elif key == "path_prepend":
            parts = value.split(os.pathsep) if isinstance(value, str) else value
            result[key] = tuple(_path(p, base) for p in parts if str(p).strip())
        elif key == "tools":
            if not isinstance(value, dict) or not set(value) <= set(TOOLS):
                raise ConfigError("tools 只支持：" + ", ".join(TOOLS))
            result[key] = {name: _path(path, base) for name, path in value.items()}
        else:
            if not isinstance(value, str):
                raise ConfigError(f"{key} 必须为字符串")
            if key == "worker_isolation" and value not in ISOLATION:
                raise ConfigError("worker_isolation 只能是 trusted-local 或 dedicated")
            result[key] = value
    return result


def _environment(environ):
    values, tools = {}, {}
    for key, value in environ.items():
        if key.startswith("OCTOPUS_TOOL_"):
            tools[key.removeprefix("OCTOPUS_TOOL_").lower()] = value
        elif key.startswith("OCTOPUS_") and key not in {"OCTOPUS_CONFIG", "OCTOPUS_URL", "OCTOPUS_API_TOKEN"}:
            values[key.removeprefix("OCTOPUS_").lower()] = value
    if tools:
        values["tools"] = tools
    return values


def _defaults(home):
    values = {
        "data_dir": "state/platform", "worker_data_dir": "state/worker", "worker_token_file": "state/worker.token",
        "crew_home": home / ".kiro/crew", "kiro_home": home / ".kiro", "routing_log": "state/provider-routing.jsonl",
        "log_dir": "state/logs", "gateway_runtime_file": "state/gateway-runtime.json",
    }
    if sys.platform == "darwin" and MAC_KIROCREW_PYTHON.exists():
        values["kirocrew_python"] = MAC_KIROCREW_PYTHON
    return values


def load(path=None, *, environ=None, overrides=None, cwd=None):
    environ = os.environ if environ is None else environ
    cwd = Path(cwd or os.getcwd())
    home = Path(environ.get("HOME") or Path.home())
    merged = _layer(_defaults(home), cwd)
    path = path or environ.get("OCTOPUS_CONFIG")
    if path:
        file = _path(path, cwd)
        try:
            with file.open("rb") as stream:
                raw = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"无法读取配置文件 {file}：{exc}") from None
        merged.update(_layer(raw, file.parent))
    environment = _layer(_environment(environ), cwd)
    tools = {**merged.get("tools", {}), **environment.pop("tools", {})}
    merged.update(environment)
    explicit = _layer({k: v for k, v in (overrides or {}).items() if v is not None}, cwd)
    tools.update(explicit.pop("tools", {}))
    merged.update(explicit)
    merged["tools"] = tools
    return Settings(**merged)
```

- [ ] **Step 4: 运行配置测试**

Run: `.venv/bin/python -m unittest tests/test_config.py -v`
Expected: 7 tests OK

- [ ] **Step 5: 写 CLI 的失败测试**

`tests/test_cli.py`:

```python
"""CLI defaults come from Settings; explicit flags still win."""
from pathlib import Path
import tempfile
import unittest

from octopus.__main__ import build_parser, settings_from_argv
from octopus.config import load


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = load(environ={"HOME": str(self.root)}, cwd=self.root,
                             overrides={"api_port": 9918, "worker_port": 9928, "crew_url": "http://localhost:15476"})

    def tearDown(self):
        self.temporary.cleanup()

    def test_serve_and_worker_defaults_follow_settings(self):
        parser = build_parser(self.settings, environ={})
        serve = parser.parse_args(["serve"])
        self.assertEqual((Path(serve.data), serve.port, serve.host), (self.settings.data_dir, 9918, "127.0.0.1"))
        worker = parser.parse_args(["worker"])
        self.assertEqual(Path(worker.data), self.settings.worker_data_dir)
        self.assertEqual(Path(worker.token_file), self.settings.worker_token_file)
        self.assertEqual(worker.port, 9928)
        self.assertEqual(worker.crew_url, "http://localhost:15476")
        self.assertEqual(Path(worker.routing_log), self.settings.routing_log)

    def test_flags_override_settings(self):
        args = build_parser(self.settings, environ={}).parse_args(["serve", "--port", "7000", "--data", "/tmp/x"])
        self.assertEqual((args.port, args.data), (7000, "/tmp/x"))

    def test_client_url_defaults_to_api_address(self):
        args = build_parser(self.settings, environ={}).parse_args(["request", "GET", "/v1/me"])
        self.assertEqual(args.url, "http://127.0.0.1:9918")
        env = {"OCTOPUS_URL": "https://ops.example"}
        self.assertEqual(build_parser(self.settings, environ=env).parse_args(["mcp"]).url, "https://ops.example")

    def test_config_flag_is_read_before_subcommand(self):
        config = self.root / "c.toml"
        config.write_text("api_port = 9500\n")
        settings, rest = settings_from_argv(["--config", str(config), "serve"], environ={"HOME": str(self.root)})
        self.assertEqual(settings.api_port, 9500)
        self.assertEqual(rest, ["serve"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: 运行确认失败**

Run: `.venv/bin/python -m unittest tests/test_cli.py -v`
Expected: FAIL，`ImportError: cannot import name 'build_parser'`

- [ ] **Step 7: 重写 `octopus/__main__.py`**

```python
"""KiroCrew ON launcher, administrative bootstrap and API client; settings come from octopus.config."""
import argparse
import getpass
import json
import os
from pathlib import Path
import secrets

from .client import Client
from .config import load
from .db import Database, dumps
from .security import create_user


def settings_from_argv(argv, environ=None):
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config")
    known, rest = pre.parse_known_args(argv)
    return load(known.config, environ=environ), rest


def build_parser(settings, environ=None):
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="TOML 配置文件；也可用 OCTOPUS_CONFIG")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--data", default=str(settings.data_dir))
    init.add_argument("--username", default="admin")
    init.add_argument("--password-env")
    serve = sub.add_parser("serve")
    serve.add_argument("--data", default=str(settings.data_dir))
    serve.add_argument("--port", type=int, default=settings.api_port)
    serve.add_argument("--host", default=settings.api_host)
    worker = sub.add_parser("worker")
    worker.add_argument("--data", default=str(settings.worker_data_dir))
    worker.add_argument("--token-file", default=str(settings.worker_token_file))
    worker.add_argument("--port", type=int, default=settings.worker_port)
    worker.add_argument("--host", default=settings.worker_host)
    worker.add_argument("--gateway-port", type=int, default=settings.gateway_port)
    worker.add_argument("--crew-url", default=settings.crew_url or None,
                        help="浏览器访问 KiroCrew 的 origin；默认 http://localhost:<gateway-port>")
    worker.add_argument("--crew-home", default=str(settings.crew_home))
    worker.add_argument("--routing-log", default=str(settings.routing_log))
    worker.add_argument("--isolation", choices=["dedicated", "trusted-local"], default=settings.worker_isolation)
    default_url = environ.get("OCTOPUS_URL", f"http://{settings.api_host}:{settings.api_port}")
    for command in ("request", "events", "mcp"):
        p = sub.add_parser(command)
        p.add_argument("--url", default=default_url)
        if command == "request":
            p.add_argument("method", choices=["GET", "POST", "DELETE"])
            p.add_argument("path")
            p.add_argument("--body-file")
        if command == "events":
            p.add_argument("run_id")
            p.add_argument("--after", type=int, default=0)
            p.add_argument("--once", action="store_true", help="Read one bounded SSE connection")
    return parser


def main(argv=None):
    settings, _ = settings_from_argv(argv)
    args = build_parser(settings).parse_args(argv)
    if args.command == "init":
        db = Database(args.data)
        password = os.environ[args.password_env] if args.password_env else getpass.getpass("Admin password (12+ chars): ")
        with db.transaction() as connection:
            if connection.execute("SELECT count(*) FROM users").fetchone()[0]:
                raise SystemExit("数据库已有用户；请通过管理员 API 管理用户。")
            user = create_user(connection, args.username, password, admin=True)
        print(dumps({"created": user, "data": str(db.root)}))
    elif args.command == "serve":
        from .engine import Engine
        from .server import server
        db = Database(args.data)
        engine = Engine(db)
        engine.start()
        print(f"KiroCrew ON API: http://{args.host}:{args.port}", flush=True)
        try:
            server(db, args.port, args.host).serve_forever()
        finally:
            engine.stopping.set()
    elif args.command == "worker":
        from .worker import Worker, serve
        token_path = Path(args.token_file)
        if not token_path.exists():
            token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            token_path.write_text(secrets.token_urlsafe(40))
            os.chmod(token_path, 0o600)
        node = Worker(args.data, token_path.read_text().strip(), args.gateway_port, args.crew_home,
                      args.isolation, args.routing_log, args.crew_url)
        print(f"KiroCrew ON Worker: http://{args.host}:{args.port} ({args.isolation})", flush=True)
        serve(node, args.port, args.host).serve_forever()
    else:
        client = Client(args.url, os.environ.get("OCTOPUS_API_TOKEN", ""))
        if args.command == "request":
            body = json.loads(Path(args.body_file).read_text()) if args.body_file else None
            print(json.dumps(client.request(args.method, args.path, body), ensure_ascii=False, indent=2))
        elif args.command == "events":
            after = args.after
            try:
                while True:
                    for event in client.events(args.run_id, after):
                        after = event["id"]
                        print(dumps(event), flush=True)
                    if args.once:
                        break
            except KeyboardInterrupt:
                pass
        else:
            from .mcp import serve
            serve(client)


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: 运行测试与全量回归**

Run: `.venv/bin/python -m unittest tests/test_cli.py tests/test_config.py -v && tools/regress.sh`
Expected: 11 tests OK；`regress: OK`

- [ ] **Step 9: 报告并提交（确认后）**

```bash
git add octopus/config.py octopus/__main__.py tests/test_config.py tests/test_cli.py
git commit -m "feat: 统一运行时配置，CLI 默认值来自配置文件与环境变量"
```

---

### Task 5: composition 包

**Files:**
- Create: `composition/__init__.py`、`composition/registry.py`、`composition/environment.py`、`composition/templates.py`、`composition/gateway.py`、`tests/test_composition.py`
- Modify: `poc.py`（全文重写为兼容层）

**Interfaces:**
- Consumes: `octopus.config.Settings`（Task 4）、`octopus.prompts`（Task 3）、`pipeline_spec`（旧版模板提示词）。
- Produces:
  - `composition.registry.BACKENDS: dict[str, str]`（5 个现行模板）、`LEGACY_BACKENDS`（5 个 `poc-pipeline-*`）、`ROUTES = {**BACKENDS, **LEGACY_BACKENDS}`
  - `composition.registry.PocProviderRegistry(original, routing_log: Path | None = None)`；`create_factory(cfg)`
  - `composition.environment.prepare(settings, environ, which=shutil.which) -> None`
  - `composition.templates.seed(*, crew_home: Path, kiro_home: Path, allowed_roots: list[Path], port: int, legacy_pipeline: bool) -> list[Path]`
  - `composition.gateway.main(argv=None)`，`--check` 打印 `<template> <provider type> <backend>` 并以 0 退出
  - `composition.registry.FEATURES = ["codex_effort_pair_v1"]`

- [ ] **Step 1: 写失败测试**

`tests/test_composition.py`:

```python
"""Composition routes poc-* templates to native factories; all paths come from Settings."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from composition import environment, registry, templates
from octopus.config import load


class FakeConfig:
    def __init__(self, sharing=False, pool=0):
        self.agent = SimpleNamespace(session_sharing=sharing, acp_backend="", member_acp_backend="kas")
        self.session = SimpleNamespace(pool_size=pool)

    def __deepcopy__(self, memo):
        clone = FakeConfig(self.agent.session_sharing, self.session.pool_size)
        clone.agent.acp_backend, clone.agent.member_acp_backend = self.agent.acp_backend, self.agent.member_acp_backend
        return clone

    def create_provider_factory(self):
        backend = self.agent.acp_backend

        def factory(session_key=None, agent=None, **kwargs):
            return SimpleNamespace(client=SimpleNamespace(backend=backend, _model=kwargs.get("model_override", "")),
                                   cwd=kwargs.get("cwd", ""))
        return factory


class FakeOriginal:
    def register_acp_backends(self):
        return "registered"

    def create_factory(self, cfg):
        return lambda session_key=None, agent=None, **kwargs: SimpleNamespace(client=SimpleNamespace(backend="ordinary"))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.log = Path(self.temporary.name) / "routing.jsonl"
        self.registry = registry.PocProviderRegistry(FakeOriginal(), routing_log=self.log)

    def tearDown(self):
        self.temporary.cleanup()

    def test_every_template_reaches_its_backend_and_is_logged(self):
        factory = self.registry.create_factory(FakeConfig())
        for name, backend in registry.ROUTES.items():
            self.assertEqual(factory(session_key="k:" + name, agent=name).client.backend, backend)
        lines = [json.loads(l) for l in self.log.read_text().splitlines()]
        self.assertEqual([l["agent"] for l in lines], list(registry.ROUTES))
        self.assertEqual(lines[0]["actual_backend"], "kiro")

    def test_ordinary_agents_use_the_original_factory(self):
        factory = self.registry.create_factory(FakeConfig())
        self.assertEqual(factory(agent="kirocrew").client.backend, "ordinary")
        self.assertEqual(self.registry.register_acp_backends(), "registered")

    def test_unknown_poc_template_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Unknown PoC template"):
            self.registry.create_factory(FakeConfig())(agent="poc-gemini")

    def test_pooling_or_sharing_is_refused(self):
        for cfg in (FakeConfig(sharing=True), FakeConfig(pool=2)):
            with self.subTest(), self.assertRaises(RuntimeError):
                self.registry.create_factory(cfg)

    def test_codex_effort_is_paired_at_the_factory_seam(self):
        effort = SimpleNamespace(is_valid_effort=lambda e: e == "high", model_supports_effort=lambda m: True)
        names = SimpleNamespace(split_effort_suffix=lambda m: (m, ""))
        with patch.dict("sys.modules", {"kiro_crew.effort": effort, "kiro_crew.model_registry": names}):
            provider = self.registry.create_factory(FakeConfig())(
                agent="poc-codex", model_override="gpt-x", reasoning_effort_override="high")
        self.assertEqual(provider.client._model, "gpt-x[high]")

    def test_config_is_not_mutated(self):
        cfg = FakeConfig()
        before = copy.deepcopy(cfg)
        self.registry.create_factory(cfg)
        self.assertEqual((cfg.agent.acp_backend, cfg.agent.member_acp_backend),
                         (before.agent.acp_backend, before.agent.member_acp_backend))


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.adapters = root / "node_modules/.bin"
        self.adapters.mkdir(parents=True)
        for name in ("claude-agent-acp", "codex-acp"):
            (self.adapters / name).write_text("#!/bin/sh\n")
        self.settings = load(environ={"HOME": str(root)}, cwd=root, overrides={
            "adapters_dir": str(self.adapters), "kirocrew_python": str(root / "kc/bin/python3.12"),
            "tools": {"claude": str(root / "t/claude"), "codex": str(root / "t/codex")}, "gateway_port": 6000})

    def tearDown(self):
        self.temporary.cleanup()

    def test_environment_comes_from_settings_without_mac_paths(self):
        env = {"PATH": "/usr/bin"}
        environment.prepare(self.settings, env, which=lambda name: None)
        self.assertEqual(env["KIROCREW_HOME"], str(self.settings.crew_home))
        self.assertEqual(env["KIRO_HOME"], str(self.settings.kiro_home))
        self.assertEqual(env["KIROCREW_PORT"], "6000")
        self.assertEqual(env["CLAUDE_AGENT_ACP_BIN"], str(self.adapters / "claude-agent-acp"))
        self.assertEqual(env["CODEX_PATH"], str(self.settings.tools["codex"]))
        self.assertEqual(env["CLAUDE_CODE_EXECUTABLE"], str(self.settings.tools["claude"]))
        parts = env["PATH"].split(":")
        self.assertEqual(parts[0], str(self.settings.kirocrew_python.parent))
        self.assertEqual(parts[-1], "/usr/bin")
        self.assertNotIn("/Applications", json.dumps(env))
        self.assertNotIn("/opt/homebrew", env["PATH"])
        self.assertTrue(self.settings.crew_home.is_dir())

    def test_missing_adapter_is_reported(self):
        (self.adapters / "codex-acp").unlink()
        with self.assertRaisesRegex(RuntimeError, "codex-acp"):
            environment.prepare(self.settings, {"PATH": ""}, which=lambda name: None)


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.crew, self.kiro, self.runs = root / "crew", root / "kiro", root / "worker"
        self.stub = SimpleNamespace(managed_mcp_spec_entry=lambda name: {"command": "native-managed-entry"})

    def tearDown(self):
        self.temporary.cleanup()

    def seed(self, legacy=False):
        with patch.dict("sys.modules", {"kiro_crew.agent": self.stub}):
            return templates.seed(crew_home=self.crew, kiro_home=self.kiro, allowed_roots=[self.runs], port=6000,
                                  legacy_pipeline=legacy)

    def test_fresh_config_allows_only_worker_roots(self):
        self.seed()
        config = json.loads((self.crew / "config.json").read_text())
        self.assertEqual(config["agent"]["subagent_cwd_allowed_roots"], [str(self.runs)])
        self.assertFalse(config["agent"]["session_sharing"])
        self.assertEqual(config["session"]["pool_size"], 0)
        self.assertEqual(config["dashboard"]["port"], 6000)
        names = sorted(p.stem for p in (self.kiro / "agents").glob("*.json"))
        self.assertEqual(names, sorted(registry.BACKENDS))
        coordinator = json.loads((self.kiro / "agents/poc-coordinator.json").read_text())
        self.assertEqual(coordinator["mcpServers"]["kirocrew-core"], {"command": "native-managed-entry"})

    def test_existing_config_and_user_agent_fields_are_preserved(self):
        self.crew.mkdir(parents=True)
        (self.crew / "config.json").write_text('{"user_config":"preserve"}\n')
        (self.kiro / "agents").mkdir(parents=True)
        (self.kiro / "agents/poc-claude.json").write_text(json.dumps({"name": "poc-claude", "model": "user-model",
                                                                       "prompt": "old", "resources": ["skill://x"]}))
        self.seed()
        self.assertEqual((self.crew / "config.json").read_text(), '{"user_config":"preserve"}\n')
        agent = json.loads((self.kiro / "agents/poc-claude.json").read_text())
        self.assertEqual((agent["model"], agent["resources"]), ("user-model", ["skill://x"]))
        self.assertIn("真实研发工作", agent["prompt"])

    def test_legacy_pipeline_templates_only_on_request(self):
        self.seed(legacy=True)
        legacy = json.loads((self.kiro / "agents/poc-pipeline-code.json").read_text())
        self.assertEqual(legacy["description"], "App PoC / claude worker")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m unittest tests/test_composition.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'composition'`

- [ ] **Step 3: `composition/__init__.py` 与 `registry.py`**

`composition/__init__.py`:

```python
"""KiroCrew composition root: route poc-* agent templates to native ACP factories."""
```

`composition/registry.py`（逻辑来自旧版 `poc.py:131-187`，路由日志路径改为参数）:

```python
"""Compose existing ACP factories; all lifecycle and permissions remain KiroCrew's."""
import copy
import json
import os
from pathlib import Path
import time

SUPPORTED_VERSIONS = {"0.7.1", "0.7.2", "0.8.0"}
FEATURES = ["codex_effort_pair_v1"]
BACKENDS = {
    "poc-coordinator": "", "poc-kiro": "", "poc-claude": "claude", "poc-codex": "codex", "poc-opencode": "opencode",
}
LEGACY_BACKENDS = {
    "poc-pipeline-coordinator": "", "poc-pipeline-plan": "", "poc-pipeline-code": "claude",
    "poc-pipeline-review": "codex", "poc-pipeline-deliver": "opencode",
}
ROUTES = {**BACKENDS, **LEGACY_BACKENDS}


class PocProviderRegistry:
    def __init__(self, original, routing_log=None):
        self.original = original
        self.routing_log = Path(routing_log) if routing_log else None

    def register_acp_backends(self):
        return self.original.register_acp_backends()

    def create_factory(self, cfg):
        if cfg.agent.session_sharing or cfg.session.pool_size:
            raise RuntimeError("PoC routing requires session_sharing=false and pool_size=0")
        factories = {}
        for backend in set(ROUTES.values()):
            scoped = copy.deepcopy(cfg)
            scoped.agent.acp_backend = backend
            scoped.agent.member_acp_backend = backend
            factories[backend] = scoped.create_provider_factory()
        ordinary = self.original.create_factory(cfg)

        def create(session_key=None, agent=None, **kwargs):
            if agent not in ROUTES:
                if agent and agent.startswith("poc-"):
                    raise ValueError(f"Unknown PoC template: {agent}")
                return ordinary(session_key=session_key, agent=agent, **kwargs)
            backend = ROUTES[agent]
            requested_model = kwargs.get("model_override")
            requested_effort = kwargs.get("reasoning_effort_override", "")
            if backend == "codex" and requested_model and requested_model != "auto" and requested_effort:
                from kiro_crew.effort import is_valid_effort, model_supports_effort
                from kiro_crew.model_registry import split_effort_suffix
                base_model, _ = split_effort_suffix(requested_model)
                if is_valid_effort(requested_effort) and model_supports_effort(base_model):
                    # spawn_run's schema rejects brackets; encode the pair Codex advertises here.
                    kwargs = {**kwargs, "model_override": f"{base_model}[{requested_effort}]"}
            provider = factories[backend](session_key=session_key, agent=agent, **kwargs)
            actual = provider.client.backend
            if actual != backend:
                raise RuntimeError(f"ACP routing mismatch: {agent}: {actual!r} != {backend!r}")
            if self.routing_log:
                event = {
                    "time": time.time(), "gateway_pid": os.getpid(), "session_key": session_key, "agent": agent,
                    "backend": backend or "kiro", "cwd": str(getattr(provider, "cwd", "") or kwargs.get("cwd", "")),
                    "actual_backend": actual or "kiro", "provider_type": type(provider).__name__,
                    "requested_model": requested_model, "requested_effort": requested_effort,
                    "wire_model": getattr(provider.client, "_model", ""),
                }
                self.routing_log.parent.mkdir(parents=True, exist_ok=True)
                with self.routing_log.open("a") as stream:
                    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            return provider

        return create
```

- [ ] **Step 4: `composition/environment.py`**

```python
"""Process environment for the composed Gateway, derived only from Settings."""
import os
import shutil


def prepare(settings, environ, which=shutil.which):
    adapters = settings.adapters_dir
    if adapters is None:
        raise RuntimeError("adapters_dir 未配置：需要包含 claude-agent-acp 与 codex-acp 的 node_modules/.bin")
    for directory in (settings.crew_home, settings.kiro_home / "agents", settings.worker_data_dir,
                      settings.routing_log.parent, settings.log_dir):
        directory.mkdir(parents=True, exist_ok=True)
    environ["KIROCREW_HOME"] = str(settings.crew_home)
    environ["KIRO_HOME"] = str(settings.kiro_home)
    environ["KIROCREW_PORT"] = str(settings.gateway_port)
    environ["PYTHONDONTWRITEBYTECODE"] = "1"
    prefix = ([settings.kirocrew_python.parent] if settings.kirocrew_python else []) + [adapters, *settings.path_prepend]
    environ["PATH"] = os.pathsep.join([str(p) for p in prefix] + [environ.get("PATH", "")])
    for variable, tool in (("CODEX_PATH", "codex"), ("CLAUDE_CODE_EXECUTABLE", "claude")):
        located = settings.tools.get(tool) or which(tool)
        if located:
            environ.setdefault(variable, str(located))
    if settings.tools.get("opencode"):
        environ.setdefault("OPENCODE_BIN", str(settings.tools["opencode"]))
    if settings.tools.get("kiro_cli"):
        environ.setdefault("KIROCREW_KIRO_BIN", str(settings.tools["kiro_cli"]))
    for variable, executable in (("CLAUDE_AGENT_ACP_BIN", "claude-agent-acp"), ("CODEX_ACP_BIN", "codex-acp")):
        adapter = adapters / executable
        if not adapter.exists():
            raise RuntimeError(f"Required adapter missing: {adapter}")
        environ[variable] = str(adapter)
```

- [ ] **Step 5: `composition/templates.py`**（逻辑来自旧版 `poc.py:66-128`）

```python
"""Write poc-* agent templates beside existing agents; never reset user fields or sessions."""
import json

from octopus import prompts as development

from .registry import BACKENDS, LEGACY_BACKENDS

LEGACY_COMMON = ("这是用户授权的本地多 ACP PoC。只操作明确指定的演示目录。"
                 "不读取凭据，不联网安装依赖，不发布、不提交。报告实际执行结果，不编造。")


def seed(*, crew_home, kiro_home, allowed_roots, port, legacy_pipeline):
    from kiro_crew.agent import managed_mcp_spec_entry
    crew_home.mkdir(parents=True, exist_ok=True)
    (kiro_home / "agents").mkdir(parents=True, exist_ok=True)
    config = {
        "agent": {
            "provider": "acp", "acp_backend": "", "default_agent": "poc-coordinator",
            "model": "auto", "sandbox": "auto", "approval_mode": "interactive",
            "session_sharing": False, "max_subagents": 3, "log_level": "INFO",
            "subagent_cwd_allowed_roots": [str(root) for root in allowed_roots],
            "subagent_result_ttl_secs": 86400,
        },
        "session": {"pool_size": 0},
        "memory": {"enabled": False},
        "dashboard": {"host": "127.0.0.1", "port": port, "onboarded": True},
        "workspaces": {"default": {"path": str(allowed_roots[0])}},
        "default_workspace": "default",
    }
    path = crew_home / "config.json"
    if not path.exists():
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    routes = {**BACKENDS, **(LEGACY_BACKENDS if legacy_pipeline else {})}
    written = []
    for name, backend in routes.items():
        coordinator = name in {"poc-coordinator", "poc-pipeline-coordinator"}
        spec = {
            "name": name,
            "description": f"Octopus development / {backend or 'Kiro CLI'}" + (" coordinator" if coordinator else " worker"),
            "model": "auto",
            "prompt": development.COMMON + (development.COORDINATOR if coordinator else development.WORKER),
            "tools": ["read", "write", "shell"] + (["@kirocrew-core"] if coordinator else []),
            "allowedTools": [], "resources": [],
            # Derive the managed invocation from the installed release so AcpRuntime can attach identity.
            "mcpServers": {"kirocrew-core": managed_mcp_spec_entry("kirocrew-core")} if coordinator else {},
            "includeMcpJson": False, "includeCrewContext": True,
        }
        if coordinator:
            spec["availableAgents"] = (
                [n for n in LEGACY_BACKENDS if n != name] if name == "poc-pipeline-coordinator"
                else ["poc-kiro", "poc-claude", "poc-codex", "poc-opencode"])
        if name.startswith("poc-pipeline-"):
            from pipeline_spec import COORDINATOR_PROMPT, WORKER_PROMPTS
            spec["description"] = f"App PoC / {backend or 'Kiro CLI'}" + (" coordinator" if coordinator else " worker")
            spec["prompt"] = LEGACY_COMMON + (COORDINATOR_PROMPT if coordinator else WORKER_PROMPTS[name.rsplit("-", 1)[1]])
        agent_path = kiro_home / "agents" / f"{name}.json"
        if agent_path.exists() and not name.startswith("poc-pipeline-"):
            # Refresh the development contract without resetting user skills, MCP, models or permissions.
            previous = json.loads(agent_path.read_text())
            spec = {**spec, **previous, "prompt": spec["prompt"], "description": spec["description"]}
        agent_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n")
        written.append(agent_path)
    return written
```

- [ ] **Step 6: `composition/gateway.py`**（逻辑来自旧版 `poc.py:190-224`）

```python
"""Start the KiroCrew Gateway in-process with PocProviderRegistry installed. Run under KiroCrew's Python."""
import argparse
import dataclasses
import json
import os
import sys
import time

from octopus.config import load

from . import environment, templates
from .registry import BACKENDS, FEATURES, SUPPORTED_VERSIONS, PocProviderRegistry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    parser.add_argument("--check", action="store_true", help="构建每个 poc-* 后端的 factory 并退出")
    parser.add_argument("--legacy-pipeline", action="store_true", help="同时写入旧版 poc-pipeline-* 模板")
    args = parser.parse_args(argv)
    settings = load(args.config)
    environment.prepare(settings, os.environ)
    from kiro_crew._ssl_compat import _ensure_ssl_certs
    _ensure_ssl_certs()
    from kiro_crew import __version__
    from kiro_crew.config import KiroCrewConfig
    from kiro_crew.platform import assert_security_floor, boot_platform, set_context
    if __version__ not in SUPPORTED_VERSIONS:
        raise SystemExit(f"不支持的 KiroCrew 版本 {__version__}；先完成契约检查再扩展支持")
    templates.seed(crew_home=settings.crew_home, kiro_home=settings.kiro_home,
                   allowed_roots=[settings.worker_data_dir], port=settings.gateway_port,
                   legacy_pipeline=args.legacy_pipeline)
    cfg = KiroCrewConfig.load()
    if cfg.agent.session_sharing or cfg.session.pool_size:
        raise SystemExit("组合要求 session_sharing=false 且 pool_size=0；请检查 KIROCREW_HOME/config.json")
    ctx = boot_platform(cfg)
    composed = dataclasses.replace(ctx, providers=PocProviderRegistry(ctx.providers, settings.routing_log))
    assert_security_floor(composed.security)
    set_context(composed)
    if args.check:
        factory = composed.providers.create_factory(cfg)
        for name in BACKENDS:
            provider = factory(session_key=f"composition-check:{name}", agent=name)
            print(name, type(provider).__name__, provider.client.backend or "kiro")
        return 0
    settings.gateway_runtime_file.parent.mkdir(parents=True, exist_ok=True)
    settings.gateway_runtime_file.write_text(json.dumps({
        "pid": os.getpid(), "home": str(settings.crew_home), "port": settings.gateway_port,
        "kirocrew_version": __version__, "composition": "PocProviderRegistry",
        "composition_features": FEATURES, "started_at": time.time(),
    }, indent=2) + "\n")
    from kiro_crew.cli import main as crew_main
    sys.argv = [sys.argv[0], "gateway", "--no-open", "--port", str(settings.gateway_port)]
    return crew_main()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7: `poc.py` 改为兼容层**

保持旧版调用方（`test_routing.py`、`test_workflow_workspace.py`、`pipeline_launch.py`、`start.sh`）不变：

```python
"""Compatibility layer for the macOS PoC entry points; the composition lives in composition/."""
from pathlib import Path
import os

from composition import environment as _environment
from composition import templates as _templates
from composition.registry import BACKENDS as _CURRENT, LEGACY_BACKENDS as _LEGACY, SUPPORTED_VERSIONS
from composition.registry import PocProviderRegistry as _Registry
from octopus.config import load as _load

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
CREW = Path.home() / ".kiro/crew"
KIRO = Path.home() / ".kiro"
PORT = 5476
BACKENDS = {**_CURRENT, **_LEGACY}
RESOURCES = Path("/Applications/KiroCrew.app/Contents/Resources")
BIN = RESOURCES / "backend-dist/kirocrew-backend-arm64/bin"
PYTHON = BIN / "python3.12"


def _settings():
    adapters = ROOT / "node_modules/.bin"
    return _load(overrides={
        "crew_home": str(CREW), "kiro_home": str(KIRO), "gateway_port": PORT,
        "worker_data_dir": str(STATE / "runs"), "routing_log": str(ROOT / "evidence/provider-routing.jsonl"),
        "gateway_runtime_file": str(STATE / "gateway-runtime.json"), "log_dir": str(STATE),
        "kirocrew_python": str(PYTHON), "adapters_dir": str(adapters),
        "path_prepend": os.pathsep.join([str(Path.home() / ".local/bin"), "/opt/homebrew/bin"]),
    })


def prepare_environment():
    _environment.prepare(_settings(), os.environ)


def seed():
    _templates.seed(crew_home=CREW, kiro_home=KIRO, allowed_roots=[STATE / "runs"], port=PORT, legacy_pipeline=True)


class PocProviderRegistry(_Registry):
    def __init__(self, original, routing_log=None):
        super().__init__(original, routing_log or ROOT / "evidence/provider-routing.jsonl")


def main():
    from composition.gateway import main as gateway_main
    import sys
    argv = ["--legacy-pipeline"] + (["--check"] if "--check" in sys.argv else [])
    os.environ.update({"OCTOPUS_CREW_HOME": str(CREW), "OCTOPUS_KIRO_HOME": str(KIRO),
                       "OCTOPUS_GATEWAY_PORT": str(PORT), "OCTOPUS_WORKER_DATA_DIR": str(STATE / "runs"),
                       "OCTOPUS_ROUTING_LOG": str(ROOT / "evidence/provider-routing.jsonl"),
                       "OCTOPUS_GATEWAY_RUNTIME_FILE": str(STATE / "gateway-runtime.json"),
                       "OCTOPUS_LOG_DIR": str(STATE), "OCTOPUS_KIROCREW_PYTHON": str(PYTHON),
                       "OCTOPUS_ADAPTERS_DIR": str(ROOT / "node_modules/.bin"),
                       "OCTOPUS_PATH_PREPEND": os.pathsep.join([str(Path.home() / ".local/bin"), "/opt/homebrew/bin"])})
    return gateway_main(argv)


if __name__ == "__main__":
    main()
```

（`/Applications` 与 `/opt/homebrew` 只保留在这个 macOS 兼容层；`composition/` 与 `octopus/` 中不得出现。）

- [ ] **Step 8: 运行新测试与全量回归**

Run: `.venv/bin/python -m unittest tests/test_composition.py -v && tools/regress.sh`
Expected: 11 tests OK；`regress: OK`（`test_workflow_workspace` 的 seed 测试经 `poc.seed()` 走新实现，`patch.multiple(poc, CREW=…, KIRO=…, STATE=…)` 仍生效）

- [ ] **Step 9: 平台包与 composition 不含 macOS 路径**

在 `tests/test_layout.py` 的 `LayoutTests` 中追加：

```python
    def test_no_mac_paths_outside_compat_layer(self):
        for path in [*(ROOT / "octopus").rglob("*.py"), *(ROOT / "composition").rglob("*.py")]:
            text = path.read_text()
            if path.name == "config.py":
                text = text.replace("/Applications/KiroCrew.app", "")  # documented macOS default only
            with self.subTest(path=path.name):
                self.assertNotIn("/Applications", text)
                self.assertNotIn("/opt/homebrew", text)
```

Run: `.venv/bin/python -m unittest tests/test_layout.py -v`
Expected: 5 tests OK

- [ ] **Step 10: 本机原生契约（只读，可选）**

只在本机已安装 KiroCrew 0.8.0 时执行，不修改任何配置：`/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12 -s -m unittest -v test_routing.py`。
Expected: 与迁入前相同的通过数；结果写入任务报告，注明“本机 macOS 原生契约”。若本机未安装，报告“未执行”。

- [ ] **Step 11: 报告并提交（确认后）**

```bash
git add composition poc.py tests/test_composition.py tests/test_layout.py
git commit -m "refactor: 组合逻辑迁入 composition 包，路径全部来自配置；poc.py 保留为 macOS 兼容层"
```

---

### Task 6: schema 迁移

**Files:**
- Create: `octopus/migrations.py`、`tests/test_migrations.py`
- Modify: `octopus/db.py:19-97`（`SCHEMA` 改为引用 `migrations.BASELINE`，`Database.__init__` 调用 `migrations.apply`）

**Interfaces:**
- Produces: `octopus.migrations.BASELINE: str`；`MIGRATIONS: list[tuple[int, str, str]]`；`apply(path: Path, migrations=MIGRATIONS, *, backup_dir: Path, clock=time.time) -> dict`（`{"from": int, "to": int, "backup": str | None}`）；`current(connection) -> int`。

- [ ] **Step 1: 写失败测试**

`tests/test_migrations.py`:

```python
"""Ordered migrations never initialize existing data and always back up before changing it."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from octopus import migrations
from octopus.db import Database

# Verbatim MVP 1.0.0 schema tail used by deployed databases (octopus/db.py before migrations).
LEGACY_TAIL = "CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);\nINSERT OR IGNORE INTO schema_version VALUES(1);\n"


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "octopus.sqlite3"
        self.backups = self.root / "backups"

    def tearDown(self):
        self.temporary.cleanup()

    def legacy_database(self):
        db = sqlite3.connect(self.path)
        db.executescript(migrations.BASELINE + LEGACY_TAIL)
        db.execute("INSERT INTO users(id,username,password,admin,active,created) VALUES('u1','alice','x',1,1,0)")
        db.commit()
        db.close()

    def version(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT max(version) FROM schema_version").fetchone()[0]

    def test_fresh_database_gets_baseline_without_backup(self):
        result = migrations.apply(self.path, backup_dir=self.backups)
        self.assertEqual(result, {"from": 0, "to": 1, "backup": None})
        self.assertEqual(self.version(), 1)

    def test_legacy_mvp_database_keeps_data(self):
        self.legacy_database()
        result = migrations.apply(self.path, backup_dir=self.backups)
        self.assertEqual(result, {"from": 1, "to": 1, "backup": None})
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT username FROM users").fetchall(), [("alice",)])

    def test_pending_migration_backs_up_then_applies(self):
        self.legacy_database()
        steps = migrations.MIGRATIONS + [(2, "add_note", "ALTER TABLE users ADD COLUMN note TEXT;")]
        result = migrations.apply(self.path, steps, backup_dir=self.backups, clock=lambda: 1_700_000_000)
        self.assertEqual((result["from"], result["to"]), (1, 2))
        backup = Path(result["backup"])
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(backup) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT max(version) FROM schema_version").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM users").fetchone()[0], 1)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT note FROM users").fetchall(), [(None,)])
        self.assertEqual(self.version(), 2)

    def test_failed_migration_rolls_back_completely(self):
        self.legacy_database()
        broken = migrations.MIGRATIONS + [(2, "broken", "ALTER TABLE users ADD COLUMN note TEXT;\nSELECT * FROM missing_table;")]
        with self.assertRaises(sqlite3.OperationalError):
            migrations.apply(self.path, broken, backup_dir=self.backups)
        self.assertEqual(self.version(), 1)
        with sqlite3.connect(self.path) as db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(users)")]
        self.assertNotIn("note", columns)

    def test_newer_database_is_refused(self):
        self.legacy_database()
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO schema_version VALUES(99)")
        with self.assertRaisesRegex(RuntimeError, "高于程序支持"):
            migrations.apply(self.path, backup_dir=self.backups)

    def test_database_class_applies_migrations(self):
        Database(self.root)
        self.assertEqual(self.version(), 1)
        Database(self.root)
        self.assertFalse(self.backups.exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m unittest tests/test_migrations.py -v`
Expected: FAIL，`ImportError: cannot import name 'migrations'`

- [ ] **Step 3: 实现迁移模块**

`octopus/migrations.py`：`BASELINE` 是 `octopus/db.py` 中原 `SCHEMA` 除最后两行（`schema_version` 建表与 `INSERT`）以外的全部语句，内容如下（与迁入版本逐字一致；执行前用 `diff` 与 `git show HEAD:octopus/db.py` 中的 SCHEMA 核对）：

```python
"""Ordered, transactional schema migrations; a consistent backup precedes any change to existing data."""
import os
from pathlib import Path
import sqlite3
import time

BASELINE = """
CREATE TABLE IF NOT EXISTS users(
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password TEXT NOT NULL,
 admin INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tokens(
 hash TEXT PRIMARY KEY, id TEXT UNIQUE NOT NULL, user_id TEXT NOT NULL REFERENCES users(id),
 scopes TEXT NOT NULL, expires REAL NOT NULL, label TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS teams(
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS members(
 team_id TEXT NOT NULL REFERENCES teams(id), user_id TEXT NOT NULL REFERENCES users(id),
 role TEXT NOT NULL CHECK(role IN ('owner','member')), PRIMARY KEY(team_id,user_id));
CREATE TABLE IF NOT EXISTS workflows(
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL,
 name_key TEXT NOT NULL, version INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
 created REAL NOT NULL, UNIQUE(owner_id,name_key));
CREATE TABLE IF NOT EXISTS workflow_versions(
 workflow_id TEXT NOT NULL REFERENCES workflows(id), version INTEGER NOT NULL,
 spec TEXT NOT NULL, author TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL,
 PRIMARY KEY(workflow_id,version));
CREATE TABLE IF NOT EXISTS grants(
 workflow_id TEXT NOT NULL REFERENCES workflows(id), kind TEXT NOT NULL CHECK(kind IN ('user','team')),
 principal TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('viewer','runner','editor')),
 PRIMARY KEY(workflow_id,kind,principal));
CREATE TABLE IF NOT EXISTS workers(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_id TEXT REFERENCES users(id),
 team_id TEXT REFERENCES teams(id), config TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
 CHECK((owner_id IS NULL) != (team_id IS NULL)));
CREATE TABLE IF NOT EXISTS runs(
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), team_id TEXT REFERENCES teams(id),
 worker_id TEXT REFERENCES workers(id), workflow_id TEXT REFERENCES workflows(id),
 snapshot TEXT NOT NULL, input TEXT NOT NULL, source TEXT NOT NULL,
 status TEXT NOT NULL, desired TEXT NOT NULL DEFAULT 'active', lifecycle TEXT NOT NULL DEFAULT 'open',
 version INTEGER NOT NULL DEFAULT 1, native_parent TEXT, workspace TEXT, error TEXT,
 created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
 run_id TEXT NOT NULL REFERENCES runs(id), id TEXT NOT NULL, ordinal INTEGER NOT NULL,
 spec TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', current_attempt TEXT,
 PRIMARY KEY(run_id,id), UNIQUE(run_id,ordinal));
CREATE TABLE IF NOT EXISTS attempts(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), task_id TEXT NOT NULL,
 number INTEGER NOT NULL, spec TEXT NOT NULL, status TEXT NOT NULL,
 native_id TEXT, context_hash TEXT, evidence TEXT NOT NULL DEFAULT '{}', result TEXT,
 error TEXT, created REAL NOT NULL, updated REAL NOT NULL,
 UNIQUE(run_id,task_id,number));
CREATE TABLE IF NOT EXISTS revisions(
 run_id TEXT NOT NULL REFERENCES runs(id), version INTEGER NOT NULL,
 content TEXT NOT NULL, actor TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL,
 PRIMARY KEY(run_id,version));
CREATE TABLE IF NOT EXISTS commands(
 actor TEXT NOT NULL REFERENCES users(id), key TEXT NOT NULL, hash TEXT NOT NULL,
 response TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(actor,key));
CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
 kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS run_events ON events(run_id,id);
CREATE INDEX IF NOT EXISTS run_owner ON runs(owner_id,team_id);
CREATE TABLE IF NOT EXISTS recoveries(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), number INTEGER NOT NULL,
 failed_task_id TEXT NOT NULL, failed_attempt_id TEXT NOT NULL REFERENCES attempts(id),
 attempt_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL, context TEXT NOT NULL,
 native_id TEXT, context_hash TEXT, result TEXT, evidence TEXT NOT NULL DEFAULT '{}',
 decision TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL,
 UNIQUE(run_id,number));
CREATE INDEX IF NOT EXISTS recovery_run ON recoveries(run_id,number);
"""

MIGRATIONS = [(1, "baseline", BASELINE)]


def _statements(script):
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                yield buffer.strip()
            buffer = ""
    if buffer.strip() and not buffer.strip().startswith("--"):
        raise ValueError("迁移脚本包含未结束的语句")


def current(db):
    db.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY)")
    return db.execute("SELECT coalesce(max(version), 0) FROM schema_version").fetchone()[0]


def _backup(db, backup_dir, version, clock):
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(clock()))
    target = backup_dir / f"octopus-v{version}-{stamp}.sqlite3"
    copy = sqlite3.connect(target)
    try:
        db.backup(copy)
        if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("迁移前备份完整性检查失败；未执行迁移")
    finally:
        copy.close()
    os.chmod(target, 0o600)
    return target


def apply(path, migrations=MIGRATIONS, *, backup_dir, clock=time.time):
    path, backup_dir = Path(path), Path(backup_dir)
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    try:
        db.execute("PRAGMA busy_timeout=10000")
        found = current(db)
        latest = max(number for number, _, _ in migrations)
        if found > latest:
            raise RuntimeError(f"数据库 schema 版本 {found} 高于程序支持的 {latest}；请使用更新的版本或从备份恢复")
        pending = sorted(m for m in migrations if m[0] > found)
        backup = _backup(db, backup_dir, found, clock) if pending and found > 0 else None
        for number, name, script in pending:
            db.execute("BEGIN IMMEDIATE")
            try:
                for statement in _statements(script):
                    db.execute(statement)
                db.execute("INSERT INTO schema_version VALUES(?)", (number,))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return {"from": found, "to": max(found, latest), "backup": str(backup) if backup else None}
    finally:
        db.close()
```

注意：`BASELINE` 若与迁入版本 `db.py` 的 SCHEMA 有任何差异，以迁入版本为准并修正本计划。

- [ ] **Step 4: `octopus/db.py` 改用迁移**

删除 `SCHEMA` 的字符串定义，改为 `from .migrations import BASELINE as SCHEMA, apply as migrate`（保留 `SCHEMA` 名称供外部引用），并把 `Database.__init__` 改为：

```python
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "octopus.sqlite3"
        with self.read() as db:
            db.execute("PRAGMA journal_mode=WAL")
        migrate(self.path, backup_dir=self.root / "backups")
        os.chmod(self.path, 0o600)
```

- [ ] **Step 5: 运行测试与全量回归**

Run: `.venv/bin/python -m unittest tests/test_migrations.py -v && tools/regress.sh`
Expected: 6 tests OK；`regress: OK`

- [ ] **Step 6: 报告并提交（确认后）**

```bash
git add octopus/migrations.py octopus/db.py tests/test_migrations.py
git commit -m "feat: schema 迁移框架，迁移前一致性备份，拒绝高于程序版本的数据库"
```

---

### Task 7: Engine 按 Session 并发推进与不可用退避

**Files:**
- Modify: `octopus/engine.py:15-88`（构造函数、`loop`、`tick`；新增 `_advance_guarded`、`_failed`、`drain`）
- Create: `tests/test_engine_concurrency.py`

**Interfaces:**
- Consumes: 现有 `Engine.advance(run)`（不改）。
- Produces: `Engine(database, request=worker_request, workers=1, retry_unavailable=0.0, clock=time.monotonic)`；`tick(wait=True) -> list[Future]`；`drain(timeout: float) -> bool`（全部在途推进在超时内结束返回 True）。
  - `workers=1, retry_unavailable=0` 与迁入前行为一致（现有测试使用默认值）；`serve` 传入配置值（Task 9）。

- [ ] **Step 1: 写失败测试**

`tests/test_engine_concurrency.py`:

```python
"""Slow Sessions do not stall others; a Session is never advanced twice at once; unavailability backs off."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from octopus.db import Database, dumps
from octopus.engine import Engine
from octopus.security import Error


def seed(db, *run_ids):
    now = time.time()
    with db.transaction() as c:
        c.execute("INSERT INTO users(id,username,password,admin,active,created) VALUES('u','u','x',1,1,?)", (now,))
        c.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('w','w','u',?)",
                  (dumps({"url": "http://127.0.0.1:1", "token": "t" * 40, "capabilities": {}}),))
        for rid in run_ids:
            c.execute("INSERT INTO runs(id,owner_id,worker_id,snapshot,input,source,status,created,updated) "
                      "VALUES(?,?,?,?,?,?,'preparing',?,?)", (rid, "u", "w", "{}", "goal", dumps({"type": "new"}), now, now))


def status(db, rid):
    with db.read() as c:
        return c.execute("SELECT status FROM runs WHERE id=?", (rid,)).fetchone()[0]


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temporary.name))
        self.release = threading.Event()
        self.active, self.peak, self.lock = {}, {}, threading.Lock()

    def tearDown(self):
        self.release.set()
        self.temporary.cleanup()

    def request(self, config, path, body=None, timeout=35):
        rid = body["run_id"]
        with self.lock:
            self.active[rid] = self.active.get(rid, 0) + 1
            self.peak[rid] = max(self.peak.get(rid, 0), self.active[rid])
        try:
            if rid == "run-slow":
                self.release.wait(10)
            return {"native_parent": "dashboard:" + rid, "workspace": "/tmp/" + rid}
        finally:
            with self.lock:
                self.active[rid] -= 1

    def test_slow_session_does_not_block_others(self):
        seed(self.db, "run-slow", "run-fast")
        engine = Engine(self.db, request=self.request, workers=2)
        engine.tick(wait=False)
        deadline = time.time() + 5
        while status(self.db, "run-fast") != "ready" and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(status(self.db, "run-fast"), "ready")
        self.assertEqual(status(self.db, "run-slow"), "preparing")
        self.release.set()
        self.assertTrue(engine.drain(5))
        self.assertEqual(status(self.db, "run-slow"), "ready")

    def test_same_session_is_never_advanced_concurrently(self):
        seed(self.db, "run-slow")
        engine = Engine(self.db, request=self.request, workers=4)
        engine.tick(wait=False)
        time.sleep(0.2)
        engine.tick(wait=False)
        engine.tick(wait=False)
        self.release.set()
        self.assertTrue(engine.drain(5))
        self.assertEqual(self.peak["run-slow"], 1)

    def test_drain_waits_for_inflight_advance(self):
        seed(self.db, "run-slow")
        engine = Engine(self.db, request=self.request, workers=1)
        engine.tick(wait=False)
        self.assertFalse(engine.drain(0.2))
        self.release.set()
        self.assertTrue(engine.drain(5))

    def test_worker_unavailable_keeps_status_and_backs_off(self):
        seed(self.db, "run-a")
        now, calls = [100.0], []

        def unavailable(config, path, body=None, timeout=35):
            calls.append(now[0])
            raise Error(503, "worker_unavailable", "Worker 未返回可核实回执")

        engine = Engine(self.db, request=unavailable, workers=1, retry_unavailable=2.0, clock=lambda: now[0])
        engine.tick()
        self.assertEqual(status(self.db, "run-a"), "preparing")
        engine.tick()
        self.assertEqual(len(calls), 1)
        now[0] += 2.1
        engine.tick()
        self.assertEqual(len(calls), 2)
        now[0] += 2.1
        engine.tick()
        self.assertEqual(len(calls), 2)  # second failure doubles the delay to 4s
        now[0] += 2.0
        engine.tick()
        self.assertEqual(len(calls), 3)

    def test_legacy_mode_still_blocks_on_prepare_failure(self):
        seed(self.db, "run-a")

        def unavailable(config, path, body=None, timeout=35):
            raise Error(503, "worker_unavailable", "Worker 未返回可核实回执")

        Engine(self.db, request=unavailable).tick()
        self.assertEqual(status(self.db, "run-a"), "blocked")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m unittest tests/test_engine_concurrency.py -v`
Expected: FAIL，`TypeError: Engine.__init__() got an unexpected keyword argument 'workers'`

- [ ] **Step 3: 修改 Engine**

在 `octopus/engine.py` 顶部 import 增加 `from concurrent.futures import ThreadPoolExecutor, wait as wait_futures`。把 `__init__`、`loop`、`tick` 替换为下列代码；`tick` 原 `except` 分支的处理逻辑**原样**移入 `_failed`，只在开头增加退避分支：

```python
class Engine:
    def __init__(self, database, request=worker_request, workers=1, retry_unavailable=0.0, clock=time.monotonic):
        self.db, self.request = database, request
        self.stopping = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="octopus-session")
        self.inflight, self.futures, self.guard = set(), set(), threading.Lock()
        self.retry_unavailable, self.clock, self.backoff = retry_unavailable, clock, {}

    def loop(self):
        while not self.stopping.is_set():
            try:
                self.tick(wait=False)
            except Exception as exc:
                # Error class only: exception messages may contain transport secrets.
                logging.error("Octopus observer cycle failed (%s)", type(exc).__name__)
            self.stopping.wait(1)

    def drain(self, timeout):
        self.stopping.set()
        with self.guard:
            pending = set(self.futures)
        done, not_done = wait_futures(pending, timeout=timeout)
        return not not_done

    def tick(self, wait=True):
        recovery.reconcile(self.db)
        with self.db.read() as db:
            active = [dict(r) for r in db.execute(
                "SELECT r.*,w.config worker_config,w.enabled worker_enabled FROM runs r "
                "JOIN workers w ON w.id=r.worker_id WHERE r.lifecycle!='deleted' AND "
                "r.status IN ('preparing','ready','running','waiting_approval','dispatch_unknown','stopping','reopening','diagnosing') "
                "ORDER BY r.created")]
        now, submitted = self.clock(), []
        for run in active:
            if self.backoff.get(run["id"], (0, 0.0))[1] > now:
                continue
            with self.guard:
                if run["id"] in self.inflight:
                    continue
                self.inflight.add(run["id"])
                future = self.pool.submit(self._advance_guarded, run)
                self.futures.add(future)
            future.add_done_callback(self._forget)
            submitted.append(future)
        if wait:
            for future in submitted:
                future.result()
        return submitted

    def _forget(self, future):
        with self.guard:
            self.futures.discard(future)

    def _advance_guarded(self, run):
        try:
            self.advance(run)
            self.backoff.pop(run["id"], None)
        except Exception as exc:
            self._failed(run, exc)
        finally:
            with self.guard:
                self.inflight.discard(run["id"])

    def _failed(self, run, exc):
        unavailable = bool(self.retry_unavailable) and getattr(exc, "code", "") == "worker_unavailable"
        if unavailable:
            failures = self.backoff.get(run["id"], (0, 0.0))[0] + 1
            self.backoff[run["id"]] = (failures, self.clock() + min(60.0, self.retry_unavailable * 2 ** (failures - 1)))
        # Error codes are stable; do not publish transport credentials/tracebacks.
        message = str(exc) if hasattr(exc, "code") else "后台操作失败，请检查 Worker 与原生状态"
        with self.db.read() as db:
            current = db.execute(
                "SELECT a.* FROM attempts a JOIN tasks t ON t.current_attempt=a.id "
                "WHERE a.run_id=? AND a.status IN ('dispatching','running','dispatch_unknown','waiting_approval') "
                "ORDER BY t.ordinal LIMIT 1", (run["id"],)).fetchone()
        if current and current["status"] == "dispatching":
            if getattr(exc, "code", "") in {"model_unavailable", "model_transport_unsupported", "invalid_request", "not_prepared"}:
                with self.db.transaction() as db:
                    db.execute("UPDATE attempts SET status='blocked',error=?,updated=? WHERE id=?",
                               (message, time.time(), current["id"]))
                    db.execute("UPDATE tasks SET status='blocked' WHERE current_attempt=?", (current["id"],))
                self.update(run["id"], "blocked", message)
            else:
                # Worker HTTP acknowledgment may be lost after native spawn.
                # Reuse this Attempt on recovery; never permit a new retry yet.
                self.update(run["id"], "dispatch_unknown", message)
            return
        if unavailable:
            # Keep the current status: the Worker may come back; the next attempt reconciles first.
            return self.update(run["id"], run["status"], message)
        if run["status"] in {"running", "dispatch_unknown", "waiting_approval", "stopping", "reopening", "diagnosing"}:
            self.update(run["id"], run["status"], message)
        else:
            self.update(run["id"], "blocked", message)
```

`_failed` 的主体与原 `tick` 中每个 Run 的 `except` 分支逐字一致（原来的 `continue` 改为 `return`）；新增的只有 `unavailable` 判断：派发中的 Attempt 仍按原逻辑进入可见的 `dispatch_unknown`，退避只决定下次何时再试。

- [ ] **Step 4: 运行测试与全量回归**

Run: `.venv/bin/python -m unittest tests/test_engine_concurrency.py -v && tools/regress.sh`
Expected: 5 tests OK；`regress: OK`（现有测试使用 `workers=1, retry_unavailable=0`，行为与迁入前一致）

- [ ] **Step 5: 报告并提交（确认后）**

```bash
git add octopus/engine.py tests/test_engine_concurrency.py
git commit -m "feat: Engine 按 Session 并发推进，同一 Session 串行，Worker 不可用时退避而不阻断"
```

---

### Task 8: Host 白名单

**Files:**
- Modify: `octopus/server.py:22`（签名）、`dispatch` 开头、文件末尾 `return ThreadingHTTPServer(...)`
- Create: `tests/test_host_allowlist.py`

**Interfaces:**
- Produces: `server(database, port=8918, host="127.0.0.1", allowed_hosts=()) -> ThreadingHTTPServer`；默认允许 `127.0.0.1:<实际端口>`、`localhost:<实际端口>`、`[::1]:<实际端口>`；不在白名单返回 `421 {"code": "host_not_allowed"}`。

- [ ] **Step 1: 写失败测试**

`tests/test_host_allowlist.py`:

```python
"""Requests must name an allowed Host; tunnels and proxies are explicit configuration."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from octopus.db import Database
from octopus.server import server


class HostAllowlistTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.httpd = server(Database(Path(self.temporary.name)), 0, allowed_hosts=("localhost:18918",))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.temporary.cleanup()

    def call(self, method, path, host, body=None, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if origin:
            headers["Origin"] = origin
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def test_loopback_with_actual_port_is_allowed(self):
        self.assertEqual(self.call("GET", "/health", f"127.0.0.1:{self.port}")[0], 200)
        self.assertEqual(self.call("GET", "/health", f"localhost:{self.port}")[0], 200)

    def test_unknown_host_is_rejected_for_api_and_static(self):
        for path in ("/health", "/", "/v1/openapi.json"):
            status, data = self.call("GET", path, "attacker.example")
            self.assertEqual(status, 421)
            self.assertEqual(json.loads(data)["code"], "host_not_allowed")

    def test_configured_tunnel_host_is_allowed(self):
        self.assertEqual(self.call("GET", "/health", "localhost:18918")[0], 200)

    def test_origin_mismatch_still_rejects_writes(self):
        status, data = self.call("POST", "/v1/login", "localhost:18918", {"username": "a", "password": "b" * 12},
                                 origin="http://attacker.example")
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m unittest tests/test_host_allowlist.py -v`
Expected: FAIL，`TypeError: server() got an unexpected keyword argument 'allowed_hosts'`

- [ ] **Step 3: 实现**

1. `octopus/server.py` 第 22 行签名改为 `def server(database, port=8918, host="127.0.0.1", allowed_hosts=()):`，并在 `service = Service(database)` 下一行加 `hosts = set()`。
2. `dispatch` 方法的 `try:` 之后第一行加入：

```python
                require(self.headers.get("Host", "") in hosts, "Host 不在允许列表；隧道或代理地址需写入 allowed_hosts",
                        421, "host_not_allowed")
```

3. 文件末尾的 `return ThreadingHTTPServer((host, port), Handler)` 替换为：

```python
    httpd = ThreadingHTTPServer((host, port), Handler)
    bound = httpd.server_address[1]
    hosts.update({f"127.0.0.1:{bound}", f"localhost:{bound}", f"[::1]:{bound}", *allowed_hosts})
    return httpd
```

- [ ] **Step 4: 运行测试与全量回归**

Run: `.venv/bin/python -m unittest tests/test_host_allowlist.py -v && tools/regress.sh`
Expected: 4 tests OK；`regress: OK`

- [ ] **Step 5: 报告并提交（确认后）**

```bash
git add octopus/server.py tests/test_host_allowlist.py
git commit -m "feat: API Host 白名单，隧道与代理地址通过 allowed_hosts 显式配置"
```

---

### Task 9: 无桌面服务入口、就绪检查与 systemd

**Files:**
- Create: `octopus/ops.py`、`deploy/systemd/octopus-api.service`、`deploy/systemd/octopus-worker@.service`、`deploy/systemd/octopus-gateway@.service`、`tests/test_service_lifecycle.py`
- Modify: `octopus/__main__.py`（`serve` / `worker` 处理 SIGTERM 并使用配置；新增 `wait-ready` 子命令）

**Interfaces:**
- Consumes: `Settings`（Task 4）、`Engine(..., workers, retry_unavailable)` 与 `drain`（Task 7）、`server(..., allowed_hosts)`（Task 8）、`composition.gateway`（Task 5）。
- Produces: `octopus.ops.wait_ready(checks: list[tuple[str, dict]], timeout: float, interval: float = 0.5, opener=urllib.request.urlopen) -> list[str]`（返回仍未就绪的 URL，空列表表示全部就绪）；`octopus.ops.runtime_marker_ok(path: Path) -> bool`；CLI `octopus wait-ready [--api] [--worker] [--gateway] [--timeout N]`。

- [ ] **Step 1: 写失败测试**

`tests/test_service_lifecycle.py`:

```python
"""Headless lifecycle: readiness probing, clean SIGTERM exit, and unit files that match the CLI."""
import configparser
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from octopus import ops
from octopus.__main__ import build_parser
from octopus.config import load

ROOT = Path(__file__).resolve().parent.parent


class ReadinessTests(unittest.TestCase):
    def test_ready_after_transient_failures(self):
        attempts = []

        def opener(request, timeout):
            attempts.append(request.full_url)
            if len(attempts) < 3:
                raise urllib.error.URLError("refused")
            return open(os.devnull, "rb")

        self.assertEqual(ops.wait_ready([("http://127.0.0.1:1/health", {})], timeout=5, interval=0.01, opener=opener), [])
        self.assertEqual(len(attempts), 3)

    def test_timeout_reports_unready_urls(self):
        def opener(request, timeout):
            raise urllib.error.URLError("refused")

        pending = ops.wait_ready([("http://127.0.0.1:1/health", {})], timeout=0.1, interval=0.02, opener=opener)
        self.assertEqual(pending, ["http://127.0.0.1:1/health"])

    def test_runtime_marker_requires_composition(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "gateway-runtime.json"
            self.assertFalse(ops.runtime_marker_ok(marker))
            marker.write_text(json.dumps({"pid": os.getpid(), "composition": "PocProviderRegistry",
                                          "composition_features": ["codex_effort_pair_v1"]}))
            self.assertTrue(ops.runtime_marker_ok(marker))
            marker.write_text(json.dumps({"pid": os.getpid(), "composition": "default"}))
            self.assertFalse(ops.runtime_marker_ok(marker))


class ServeProcessTests(unittest.TestCase):
    def test_serve_exits_cleanly_on_sigterm(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "octopus.toml"
            config.write_text(f'data_dir = "{temporary}/platform"\n')
            process = subprocess.Popen([sys.executable, "-m", "octopus", "--config", str(config), "serve", "--port", "0"],
                                       cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                line = process.stdout.readline()
                port = int(line.rsplit(":", 1)[1])
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=15), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                process.stdout.close()


class UnitFileTests(unittest.TestCase):
    def units(self):
        for path in sorted((ROOT / "deploy/systemd").glob("*.service")):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            parser.read(path)
            yield path.name, parser

    def test_units_use_existing_commands_and_separate_users(self):
        names = {}
        subcommands = build_parser(load(environ={"HOME": "/tmp"}, cwd="/tmp"))._subparsers._group_actions[0].choices
        for name, unit in self.units():
            service = unit["Service"]
            names[name] = service["User"]
            command = service["ExecStart"]
            if "-m octopus" in command:
                self.assertIn(command.split()[-1], subcommands)
            else:
                self.assertIn("-m composition.gateway", command)
                self.assertTrue((ROOT / "composition/gateway.py").is_file())
            self.assertEqual(service["KillSignal"], "SIGTERM")
            self.assertIn("ReadWritePaths", service)
        self.assertEqual(names["octopus-api.service"], "octopus")
        self.assertEqual(names["octopus-worker@.service"], "crew-%i")
        self.assertEqual(names["octopus-gateway@.service"], "crew-%i")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m unittest tests/test_service_lifecycle.py -v`
Expected: FAIL，`ImportError: cannot import name 'ops'`

- [ ] **Step 3: 实现 `octopus/ops.py`**

```python
"""Operational probes for service managers; no credentials are printed."""
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request


def wait_ready(checks, timeout, interval=0.5, opener=urllib.request.urlopen):
    deadline, pending = time.monotonic() + timeout, list(checks)
    while pending:
        remaining = []
        for url, headers in pending:
            try:
                with opener(urllib.request.Request(url, headers=headers), timeout=5) as response:
                    if getattr(response, "status", 200) != 200:
                        remaining.append((url, headers))
            except (urllib.error.URLError, OSError, ValueError):
                remaining.append((url, headers))
        pending = remaining
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(interval)
    return [url for url, _ in pending]


def runtime_marker_ok(path):
    try:
        marker = json.loads(Path(path).read_text())
        os.kill(int(marker["pid"]), 0)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return marker.get("composition") == "PocProviderRegistry" and "codex_effort_pair_v1" in marker.get("composition_features", [])
```

- [ ] **Step 4: `__main__.py` 的 serve / worker / wait-ready**

在 `build_parser` 中 `sub.add_parser("worker")` 段之后加入：

```python
    ready = sub.add_parser("wait-ready", help="等待服务就绪（供 systemd ExecStartPost 使用）")
    ready.add_argument("--api", action="store_true")
    ready.add_argument("--worker", action="store_true")
    ready.add_argument("--gateway", action="store_true")
    ready.add_argument("--timeout", type=float, default=60)
```

把 `main` 中的 `serve` 与 `worker` 分支替换，并新增 `wait-ready` 分支（顶部 import 增加 `import signal`、`import threading`）：

```python
    elif args.command == "serve":
        from .engine import Engine
        from .server import server
        db = Database(args.data)
        engine = Engine(db, workers=settings.engine_workers, retry_unavailable=settings.retry_unavailable)
        httpd = server(db, args.port, args.host, allowed_hosts=settings.allowed_hosts)
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=httpd.shutdown, daemon=True).start())
        engine.start()
        print(f"KiroCrew ON API: http://{args.host}:{httpd.server_address[1]}", flush=True)
        try:
            httpd.serve_forever()
        finally:
            engine.drain(30)
            httpd.server_close()
    elif args.command == "worker":
        from .worker import Worker, serve
        token_path = Path(args.token_file)
        if not token_path.exists():
            token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            token_path.write_text(secrets.token_urlsafe(40))
            os.chmod(token_path, 0o600)
        node = Worker(args.data, token_path.read_text().strip(), args.gateway_port, args.crew_home,
                      args.isolation, args.routing_log, args.crew_url)
        httpd = serve(node, args.port, args.host)
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=httpd.shutdown, daemon=True).start())
        print(f"KiroCrew ON Worker: http://{args.host}:{httpd.server_address[1]} ({args.isolation})", flush=True)
        try:
            httpd.serve_forever()
        finally:
            httpd.server_close()
    elif args.command == "wait-ready":
        from .ops import runtime_marker_ok, wait_ready
        checks = []
        if args.api:
            checks.append((f"http://{settings.api_host}:{settings.api_port}/health", {}))
        if args.worker:
            token = settings.worker_token_file.read_text().strip()
            checks.append((f"http://{settings.worker_host}:{settings.worker_port}/health", {"Authorization": "Bearer " + token}))
        if args.gateway:
            checks.append((f"http://127.0.0.1:{settings.gateway_port}/api/ready", {}))
        pending = wait_ready(checks, args.timeout)
        if args.gateway and not pending and not runtime_marker_ok(settings.gateway_runtime_file):
            pending.append(f"{settings.gateway_runtime_file}（未加载 PocProviderRegistry 组合）")
        if pending:
            raise SystemExit("未就绪：" + "；".join(pending))
        print("ready")
```

- [ ] **Step 5: systemd 单元**

`deploy/systemd/octopus-api.service`:

```ini
[Unit]
Description=KiroCrew ON platform API, console and Engine
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=octopus
Group=octopus
WorkingDirectory=/opt/kirocrew-on/current
Environment=PYTHONPATH=/opt/kirocrew-on/current
ExecStart=/opt/kirocrew-on/venv/bin/python -m octopus --config /etc/kirocrew-on/platform.toml serve
ExecStartPost=/opt/kirocrew-on/venv/bin/python -m octopus --config /etc/kirocrew-on/platform.toml wait-ready --api --timeout 60
KillSignal=SIGTERM
TimeoutStopSec=45
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/octopus/platform /var/log/kirocrew-on

[Install]
WantedBy=multi-user.target
```

`deploy/systemd/octopus-gateway@.service`:

```ini
[Unit]
Description=KiroCrew Gateway with Octopus composition for boundary %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=crew-%i
Group=crew-%i
WorkingDirectory=/var/lib/octopus/boundaries/%i
Environment=PYTHONPATH=/opt/kirocrew-on/current
Environment=HOME=/var/lib/octopus/boundaries/%i/home
ExecStart=/opt/kirocrew/current/venv/bin/python -m composition.gateway --config /etc/kirocrew-on/boundary-%i.toml
ExecStartPost=/opt/kirocrew-on/venv/bin/python -m octopus --config /etc/kirocrew-on/boundary-%i.toml wait-ready --gateway --timeout 90
KillSignal=SIGTERM
TimeoutStopSec=60
Restart=on-failure
RestartSec=10
UMask=0077
PrivateTmp=yes
ReadWritePaths=/var/lib/octopus/boundaries/%i

[Install]
WantedBy=multi-user.target
```

`deploy/systemd/octopus-worker@.service`:

```ini
[Unit]
Description=KiroCrew ON Worker for boundary %i
After=octopus-gateway@%i.service
Wants=octopus-gateway@%i.service

[Service]
Type=simple
User=crew-%i
Group=crew-%i
WorkingDirectory=/var/lib/octopus/boundaries/%i
Environment=PYTHONPATH=/opt/kirocrew-on/current
Environment=HOME=/var/lib/octopus/boundaries/%i/home
ExecStart=/opt/kirocrew-on/venv/bin/python -m octopus --config /etc/kirocrew-on/boundary-%i.toml worker
ExecStartPost=/opt/kirocrew-on/venv/bin/python -m octopus --config /etc/kirocrew-on/boundary-%i.toml wait-ready --worker --timeout 60
KillSignal=SIGTERM
TimeoutStopSec=45
Restart=on-failure
RestartSec=5
UMask=0077
PrivateTmp=yes
ReadWritePaths=/var/lib/octopus/boundaries/%i

[Install]
WantedBy=multi-user.target
```

（Gateway 与 Worker 单元暂不加 `ProtectSystem=strict` / `NoNewPrivileges`：KiroCrew 沙箱需要创建 user namespace，其与这些加固项的兼容性在 Task 16 实测后再决定，结论写入运维手册。）

- [ ] **Step 6: 运行测试与全量回归**

Run: `.venv/bin/python -m unittest tests/test_service_lifecycle.py -v && tools/regress.sh`
Expected: 5 tests OK；`regress: OK`

- [ ] **Step 7: 报告并提交（确认后）**

```bash
git add octopus/ops.py octopus/__main__.py deploy/systemd tests/test_service_lifecycle.py
git commit -m "feat: 无桌面服务入口、SIGTERM 收尾、wait-ready 就绪检查与 systemd 单元"
```

---

### Task 10: 备份、归档与独立目录恢复校验

**Files:**
- Create: `octopus/backup.py`、`tests/test_backup.py`
- Modify: `octopus/__main__.py`（新增 `backup`、`archive`、`restore-verify` 子命令）

**Interfaces:**
- Produces:
  - `octopus.backup.backup_database(source: Path, dest_dir: Path, clock=time.time) -> dict`（写 `<name>.sqlite3` 与 `<name>.json` manifest：`file`、`sha256`、`integrity`、`schema_version`、`tables`、`created`）
  - `octopus.backup.archive_directory(source: Path, dest_dir: Path, name: str, exclude: tuple[str, ...] = ()) -> dict`（`.tar.gz` 0600，manifest：`file`、`sha256`、`files`）
  - `octopus.backup.restore_verify(manifest: Path, into: Path) -> dict`（`{"ok": True, "tables": {...}}`；sha256 / 完整性 / 行数不符抛 `ValueError`）

- [ ] **Step 1: 写失败测试**

`tests/test_backup.py`:

```python
"""Backups are consistent under concurrent writes and restore into a separate directory only."""
import json
from pathlib import Path
import tarfile
import tempfile
import threading
import time
import unittest

from octopus import backup
from octopus.db import Database


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db = Database(self.root / "platform")
        with self.db.transaction() as c:
            c.execute("INSERT INTO users(id,username,password,admin,active,created) VALUES('u','u','x',1,1,0)")

    def tearDown(self):
        self.temporary.cleanup()

    def test_backup_during_writes_is_consistent_and_restores_elsewhere(self):
        stop = threading.Event()

        def writer():
            n = 0
            while not stop.is_set():
                with self.db.transaction() as c:
                    c.execute("INSERT INTO teams(id,name,created) VALUES(?,?,?)", (f"t{n}", f"team{n}", time.time()))
                n += 1

        thread = threading.Thread(target=writer)
        thread.start()
        time.sleep(0.1)
        manifest = backup.backup_database(self.db.path, self.root / "backups")
        stop.set()
        thread.join()
        self.assertEqual(manifest["integrity"], "ok")
        self.assertEqual(manifest["schema_version"], 1)
        path = self.root / "backups" / manifest["file"]
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        report = backup.restore_verify(path.with_suffix(".json"), self.root / "restore")
        self.assertTrue(report["ok"])
        self.assertEqual(report["tables"]["users"], 1)
        self.assertEqual(report["tables"]["teams"], manifest["tables"]["teams"])

    def test_restore_refuses_non_empty_target_and_tampered_file(self):
        manifest = backup.backup_database(self.db.path, self.root / "backups")
        target = self.root / "busy"
        target.mkdir()
        (target / "keep").write_text("x")
        with self.assertRaisesRegex(ValueError, "非空"):
            backup.restore_verify(self.root / "backups" / (Path(manifest["file"]).stem + ".json"), target)
        with (self.root / "backups" / manifest["file"]).open("ab") as stream:
            stream.write(b"tamper")
        with self.assertRaisesRegex(ValueError, "sha256"):
            backup.restore_verify(self.root / "backups" / (Path(manifest["file"]).stem + ".json"), self.root / "new")

    def test_archive_directory_excludes_patterns(self):
        source = self.root / "boundary"
        (source / "worker/run-1").mkdir(parents=True)
        (source / "worker/run-1/handoff.md").write_text("h")
        (source / "home/.cache").mkdir(parents=True)
        (source / "home/.cache/big").write_text("c")
        manifest = backup.archive_directory(source, self.root / "backups", "boundary-a", exclude=("home/.cache/*",))
        archive = self.root / "backups" / manifest["file"]
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        with tarfile.open(archive) as tar:
            names = tar.getnames()
        self.assertIn("worker/run-1/handoff.md", names)
        self.assertNotIn("home/.cache/big", names)
        self.assertEqual(manifest["files"], 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m unittest tests/test_backup.py -v`
Expected: FAIL，`ImportError: cannot import name 'backup'`

- [ ] **Step 3: 实现 `octopus/backup.py`**

```python
"""Consistent SQLite backups, directory archives, and restore verification into a separate directory."""
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import time


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        tables = {name: db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in sorted(names)}
        version = db.execute("SELECT coalesce(max(version),0) FROM schema_version").fetchone()[0] if "schema_version" in tables else 0
        return integrity, tables, version
    finally:
        db.close()


def _manifest(path, values):
    path.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n")
    os.chmod(path, 0o600)
    return values


def backup_database(source, dest_dir, clock=time.time):
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(clock()))
    target = dest_dir / f"octopus-{stamp}.sqlite3"
    origin, copy = sqlite3.connect(source, timeout=10), sqlite3.connect(target)
    try:
        origin.backup(copy)
    finally:
        copy.close()
        origin.close()
    os.chmod(target, 0o600)
    integrity, tables, version = _inspect(target)
    return _manifest(target.with_suffix(".json"), {
        "file": target.name, "sha256": _sha256(target), "integrity": integrity,
        "schema_version": version, "tables": tables, "created": clock(), "source": str(source)})


def archive_directory(source, dest_dir, name, exclude=()):
    source, dest_dir = Path(source), Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = dest_dir / f"{name}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.tar.gz"
    count = 0
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source).as_posix()
            if path.is_file() and not path.is_symlink() and not any(fnmatch.fnmatch(relative, p) for p in exclude):
                tar.add(path, arcname=relative)
                count += 1
    return _manifest(dest_dir / (target.name.removesuffix(".tar.gz") + ".json"),
                     {"file": target.name, "sha256": _sha256(target), "files": count, "source": str(source)})


def restore_verify(manifest, into):
    manifest, into = Path(manifest), Path(into)
    values = json.loads(manifest.read_text())
    backup = manifest.parent / values["file"]
    if into.exists() and any(into.iterdir()):
        raise ValueError(f"恢复目标目录非空：{into}；请指定新的独立目录")
    if _sha256(backup) != values["sha256"]:
        raise ValueError("备份文件 sha256 与 manifest 不一致")
    into.mkdir(parents=True, exist_ok=True, mode=0o700)
    restored = into / "octopus.sqlite3"
    shutil.copy2(backup, restored)
    integrity, tables, version = _inspect(restored)
    if integrity != "ok" or tables != values["tables"] or version != values["schema_version"]:
        raise ValueError("恢复后的完整性、行数或 schema 版本与 manifest 不一致")
    return {"ok": True, "path": str(restored), "tables": tables, "schema_version": version}
```

- [ ] **Step 4: CLI 子命令**

在 `build_parser` 中加入：

```python
    backup_cmd = sub.add_parser("backup", help="平台数据库一致性备份")
    backup_cmd.add_argument("--dest", required=True)
    archive = sub.add_parser("archive", help="目录归档（Worker 数据、执行边界）")
    archive.add_argument("source")
    archive.add_argument("--dest", required=True)
    archive.add_argument("--name", required=True)
    archive.add_argument("--exclude", action="append", default=[])
    verify = sub.add_parser("restore-verify", help="在独立目录恢复并校验备份")
    verify.add_argument("manifest")
    verify.add_argument("--into", required=True)
```

在 `main` 中加入分支：

```python
    elif args.command == "backup":
        from .backup import backup_database
        print(dumps(backup_database(settings.data_dir / "octopus.sqlite3", Path(args.dest))))
    elif args.command == "archive":
        from .backup import archive_directory
        print(dumps(archive_directory(Path(args.source), Path(args.dest), args.name, tuple(args.exclude))))
    elif args.command == "restore-verify":
        from .backup import restore_verify
        print(dumps(restore_verify(Path(args.manifest), Path(args.into))))
```

- [ ] **Step 5: 运行测试与全量回归**

Run: `.venv/bin/python -m unittest tests/test_backup.py -v && tools/regress.sh`
Expected: 3 tests OK；`regress: OK`

- [ ] **Step 6: 报告并提交（确认后）**

```bash
git add octopus/backup.py octopus/__main__.py tests/test_backup.py
git commit -m "feat: 平台数据库一致性备份、目录归档与独立目录恢复校验"
```

---

### Task 11: EC2 部署材料

**Files:**
- Create: `deploy/aws/kirocrew-on-ec2.yaml`、`deploy/install/al2023.sh`、`deploy/versions.env`、`deploy/config/platform.toml.example`、`deploy/config/boundary.toml.example`、`tests/test_deploy_assets.py`

**Interfaces:**
- Consumes: systemd 单元（Task 9）、配置项（Task 4）。
- Produces: Part B 使用的模板与脚本；`deploy/versions.env` 的变量名：`KIROCREW_VERSION`、`KIROCREW_WHEEL_URL`、`KIROCREW_WHEEL_SHA256`、`KIRO_CLI_URL`、`KIRO_CLI_SHA256`、`CLAUDE_CODE_VERSION`、`CODEX_VERSION`、`OPENCODE_VERSION`、`NODE_PACKAGE`、`PYTHON_PACKAGE`。

- [ ] **Step 1: 写失败测试**

`tests/test_deploy_assets.py`:

```python
"""Deployment assets: SSM-only access, encrypted storage, pinned versions, no macOS assumptions."""
from pathlib import Path
import shutil
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parent.parent


class CfnLoader(yaml.SafeLoader):
    pass


CfnLoader.add_multi_constructor("!", lambda loader, suffix, node: {suffix: (
    loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else
    loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode) else loader.construct_mapping(node))})


class DeployAssetTests(unittest.TestCase):
    def template(self):
        return yaml.load((ROOT / "deploy/aws/kirocrew-on-ec2.yaml").read_text(), Loader=CfnLoader)

    def test_template_is_ssm_only_and_encrypted(self):
        resources = self.template()["Resources"]
        group = next(r for r in resources.values() if r["Type"] == "AWS::EC2::SecurityGroup")
        self.assertNotIn("SecurityGroupIngress", group["Properties"])
        role = next(r for r in resources.values() if r["Type"] == "AWS::IAM::Role")
        self.assertIn("arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore", role["Properties"]["ManagedPolicyArns"])
        instance = next(r for r in resources.values() if r["Type"] == "AWS::EC2::Instance")["Properties"]
        self.assertTrue(instance["BlockDeviceMappings"][0]["Ebs"]["Encrypted"])
        self.assertEqual(instance["MetadataOptions"]["HttpTokens"], "required")
        self.assertNotIn("KeyName", instance)

    def test_capacity_parameters_have_no_unverified_defaults(self):
        parameters = self.template()["Parameters"]
        for name in ("InstanceType", "SubnetId", "VpcId", "VolumeSizeGiB"):
            self.assertNotIn("Default", parameters[name], name)
        self.assertEqual(parameters["ImageId"]["Default"],
                         "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64")

    def test_install_script_is_valid_bash_without_mac_assumptions(self):
        script = ROOT / "deploy/install/al2023.sh"
        subprocess.run(["bash", "-n", str(script)], check=True)
        text = script.read_text()
        for forbidden in ("/Applications", "/opt/homebrew", "zsh", "lsof", "open "):
            self.assertNotIn(forbidden, text)
        if shutil.which("shellcheck"):
            subprocess.run(["shellcheck", "-S", "warning", str(script)], check=True)

    def test_versions_file_declares_every_pin(self):
        values = dict(line.split("=", 1) for line in (ROOT / "deploy/versions.env").read_text().splitlines()
                      if line and not line.startswith("#"))
        for key in ("KIROCREW_VERSION", "KIROCREW_WHEEL_URL", "KIROCREW_WHEEL_SHA256", "KIRO_CLI_URL", "KIRO_CLI_SHA256",
                    "CLAUDE_CODE_VERSION", "CODEX_VERSION", "OPENCODE_VERSION", "NODE_PACKAGE", "PYTHON_PACKAGE"):
            self.assertIn(key, values)

    def test_config_examples_load(self):
        from octopus.config import load
        platform = load(ROOT / "deploy/config/platform.toml.example", environ={"HOME": "/tmp"}, cwd="/tmp")
        self.assertEqual(platform.data_dir, Path("/var/lib/octopus/platform"))
        self.assertIn("localhost:18918", platform.allowed_hosts)
        boundary = load(ROOT / "deploy/config/boundary.toml.example", environ={"HOME": "/tmp"}, cwd="/tmp")
        self.assertEqual(boundary.worker_data_dir, Path("/var/lib/octopus/boundaries/personal/worker"))
        self.assertEqual(boundary.crew_home, Path("/var/lib/octopus/boundaries/personal/crew"))
        self.assertEqual(set(boundary.tools), {"kiro_cli", "claude", "codex", "opencode"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m unittest tests/test_deploy_assets.py -v`
Expected: FAIL，`FileNotFoundError`（模板不存在）

- [ ] **Step 3: CloudFormation 模板**

`deploy/aws/kirocrew-on-ec2.yaml`:

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: >-
  KiroCrew ON single-host M1 environment. SSM-only access (no inbound rules), encrypted gp3 root volume,
  IMDSv2. Capacity parameters have no defaults on purpose: they come from the target environment.
Parameters:
  InstanceType:
    Type: String
    Description: 由目标环境给出；不是经过验证的最低配置
  VpcId:
    Type: AWS::EC2::VPC::Id
  SubnetId:
    Type: AWS::EC2::Subnet::Id
    Description: 需能经 NAT 或 VPC 端点访问 SSM 与软件源
  VolumeSizeGiB:
    Type: Number
    MinValue: 20
  ImageId:
    Type: AWS::SSM::Parameter::Value<AWS::EC2::Image::Id>
    Default: /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64
Resources:
  HostSecurityGroup:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: KiroCrew ON host - no inbound; access through SSM Session Manager only
      VpcId: !Ref VpcId
      SecurityGroupEgress:
        - IpProtocol: "-1"
          CidrIp: 0.0.0.0/0
          Description: Outbound for SSM, package sources and model services; restrict per environment
  HostRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal: {Service: ec2.amazonaws.com}
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
  HostProfile:
    Type: AWS::IAM::InstanceProfile
    Properties:
      Roles: [!Ref HostRole]
  Host:
    Type: AWS::EC2::Instance
    Properties:
      ImageId: !Ref ImageId
      InstanceType: !Ref InstanceType
      SubnetId: !Ref SubnetId
      IamInstanceProfile: !Ref HostProfile
      SecurityGroupIds: [!Ref HostSecurityGroup]
      MetadataOptions:
        HttpTokens: required
        HttpEndpoint: enabled
      BlockDeviceMappings:
        - DeviceName: /dev/xvda
          Ebs:
            VolumeType: gp3
            VolumeSize: !Ref VolumeSizeGiB
            Encrypted: true
            DeleteOnTermination: false
      Tags:
        - {Key: Name, Value: kirocrew-on-m1}
        - {Key: Project, Value: kirocrew-on}
Outputs:
  InstanceId:
    Value: !Ref Host
```

- [ ] **Step 4: 版本与配置样例**

`deploy/versions.env`:

```bash
# 固定版本。macOS 本机观测值（2026-10-10）仅作起点；Linux 实际版本在 Task 16 探测后更新并记录来源。
KIROCREW_VERSION=0.8.0
# KiroCrew Linux 安装包地址与校验和：Task 16 依据官方安装文档确定后填写；为空时安装脚本拒绝继续。
KIROCREW_WHEEL_URL=
KIROCREW_WHEEL_SHA256=
KIRO_CLI_URL=
KIRO_CLI_SHA256=
CLAUDE_CODE_VERSION=2.1.296
CODEX_VERSION=0.153.4
OPENCODE_VERSION=1.18.30
NODE_PACKAGE=nodejs22
PYTHON_PACKAGE=python3.12
```

`deploy/config/platform.toml.example`:

```toml
# /etc/kirocrew-on/platform.toml —— 平台（OS 用户 octopus）
data_dir = "/var/lib/octopus/platform"
log_dir = "/var/log/kirocrew-on"
api_host = "127.0.0.1"
api_port = 8918
# SSM 端口转发后浏览器使用的 Host；按实际转发端口填写
allowed_hosts = ["localhost:18918"]
engine_workers = 4
retry_unavailable = 2.0
```

`deploy/config/boundary.toml.example`:

```toml
# /etc/kirocrew-on/boundary-<边界>.toml —— 执行边界（OS 用户 crew-<边界>）；把 personal 替换为边界名
worker_data_dir = "/var/lib/octopus/boundaries/personal/worker"
worker_token_file = "/var/lib/octopus/boundaries/personal/worker.token"
crew_home = "/var/lib/octopus/boundaries/personal/crew"
kiro_home = "/var/lib/octopus/boundaries/personal/kiro"
routing_log = "/var/lib/octopus/boundaries/personal/provider-routing.jsonl"
gateway_runtime_file = "/var/lib/octopus/boundaries/personal/gateway-runtime.json"
log_dir = "/var/lib/octopus/boundaries/personal/logs"
worker_host = "127.0.0.1"
worker_port = 8928
worker_isolation = "trusted-local"
gateway_port = 5476
# 原生入口经 SSM 转发后的浏览器 origin
crew_url = "http://localhost:15476"
kirocrew_python = "/opt/kirocrew/current/venv/bin/python"
adapters_dir = "/opt/kirocrew-on/current/node_modules/.bin"
path_prepend = ["/opt/kirocrew-tools/bin"]

[tools]
kiro_cli = "/opt/kirocrew-tools/bin/kiro-cli"
claude = "/opt/kirocrew-tools/bin/claude"
codex = "/opt/kirocrew-tools/bin/codex"
opencode = "/opt/kirocrew-tools/bin/opencode"
```

- [ ] **Step 5: 安装脚本**

`deploy/install/al2023.sh`:

```bash
#!/usr/bin/env bash
# Idempotent host preparation for Amazon Linux 2023 x86_64. Run as root from a kirocrew-on checkout:
#   sudo deploy/install/al2023.sh <boundary-name>
# Never starts services and never writes tool credentials; each tool is authenticated later as crew-<boundary>.
set -euo pipefail
BOUNDARY="${1:?用法: al2023.sh <边界名，如 personal>}"
[[ "$BOUNDARY" =~ ^[a-z][a-z0-9-]{1,30}$ ]] || { echo "边界名只允许小写字母、数字和连字符" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "需要 root" >&2; exit 2; }
SOURCE="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=../versions.env
source "$SOURCE/deploy/versions.env"
[ -n "$KIROCREW_WHEEL_URL" ] && [ -n "$KIROCREW_WHEEL_SHA256" ] || {
  echo "deploy/versions.env 中 KIROCREW_WHEEL_URL / KIROCREW_WHEEL_SHA256 未填写（见计划 Task 16）" >&2; exit 3; }

dnf install -y git tar gzip jq "$PYTHON_PACKAGE" "$NODE_PACKAGE"
PYTHON="$(command -v "$PYTHON_PACKAGE")"

id octopus >/dev/null 2>&1 || useradd --system --home-dir /var/lib/octopus/platform --shell /sbin/nologin octopus
CREW="crew-$BOUNDARY"
BASE="/var/lib/octopus/boundaries/$BOUNDARY"
id "$CREW" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$BASE/home" --shell /bin/bash "$CREW"

install -d -m 0700 -o octopus -g octopus /var/lib/octopus/platform
install -d -m 0750 -o octopus -g octopus /var/log/kirocrew-on
install -d -m 0700 -o "$CREW" -g "$CREW" "$BASE" "$BASE/home" "$BASE/worker" "$BASE/crew" "$BASE/kiro" "$BASE/logs"
install -d -m 0755 /etc/kirocrew-on /opt/kirocrew-on/releases /opt/kirocrew /opt/kirocrew-tools

RELEASE="$(git -C "$SOURCE" rev-parse --short HEAD)"
if [ ! -d "/opt/kirocrew-on/releases/$RELEASE" ]; then
  git -C "$SOURCE" archive --format=tar --prefix="$RELEASE/" HEAD | tar -x -C /opt/kirocrew-on/releases
fi
ln -sfn "/opt/kirocrew-on/releases/$RELEASE" /opt/kirocrew-on/current
[ -x /opt/kirocrew-on/venv/bin/python ] || "$PYTHON" -m venv /opt/kirocrew-on/venv
/opt/kirocrew-on/venv/bin/python -m pip install -r /opt/kirocrew-on/current/requirements-workflow.txt
(cd /opt/kirocrew-on/current && npm ci --omit=dev)

KC="/opt/kirocrew/$KIROCREW_VERSION"
if [ ! -x "$KC/venv/bin/python" ]; then
  "$PYTHON" -m venv "$KC/venv"
  WHEEL="$(mktemp -d)/kirocrew-$KIROCREW_VERSION.whl"
  curl -fsSL "$KIROCREW_WHEEL_URL" -o "$WHEEL"
  echo "$KIROCREW_WHEEL_SHA256  $WHEEL" | sha256sum -c -
  "$KC/venv/bin/python" -m pip install "$WHEEL"
fi
ln -sfn "$KC" /opt/kirocrew/current

npm install --global --prefix /opt/kirocrew-tools \
  "@anthropic-ai/claude-code@$CLAUDE_CODE_VERSION" "@openai/codex@$CODEX_VERSION" "opencode-ai@$OPENCODE_VERSION"
if [ -n "$KIRO_CLI_URL" ] && [ ! -x /opt/kirocrew-tools/bin/kiro-cli ]; then
  ARCHIVE="$(mktemp -d)/kiro-cli.zip"
  curl -fsSL "$KIRO_CLI_URL" -o "$ARCHIVE"
  echo "$KIRO_CLI_SHA256  $ARCHIVE" | sha256sum -c -
  echo "kiro-cli 安装包已校验：$ARCHIVE；按 Task 16 记录的官方步骤安装到 /opt/kirocrew-tools/bin" >&2
fi

[ -f /etc/kirocrew-on/platform.toml ] || install -m 0640 -o root -g octopus \
  /opt/kirocrew-on/current/deploy/config/platform.toml.example /etc/kirocrew-on/platform.toml
if [ ! -f "/etc/kirocrew-on/boundary-$BOUNDARY.toml" ]; then
  sed "s#/boundaries/personal/#/boundaries/$BOUNDARY/#g" \
    /opt/kirocrew-on/current/deploy/config/boundary.toml.example > "/etc/kirocrew-on/boundary-$BOUNDARY.toml"
  chown "root:$CREW" "/etc/kirocrew-on/boundary-$BOUNDARY.toml"
  chmod 0640 "/etc/kirocrew-on/boundary-$BOUNDARY.toml"
fi

install -m 0644 /opt/kirocrew-on/current/deploy/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
echo "完成。下一步：以 $CREW 身份为各工具初始化认证，然后按 docs/OPERATIONS.md 启动服务。"
```

- [ ] **Step 6: 运行测试与全量回归**

Run: `.venv/bin/python -m unittest tests/test_deploy_assets.py -v && tools/regress.sh`
Expected: 5 tests OK（未安装 shellcheck 时跳过该子检查）；`regress: OK`

- [ ] **Step 7: 报告并提交（确认后）**

```bash
git add deploy tests/test_deploy_assets.py
git commit -m "feat: EC2 部署材料——SSM-only CloudFormation、AL2023 安装脚本、版本与配置样例"
```

---

### Task 12: 运维手册

**Files:**
- Create: `docs/OPERATIONS.md`
- Modify: `README.md`（文档索引增加运维手册）

- [ ] **Step 1: 编写运维手册**

`docs/OPERATIONS.md` 必须包含以下章节，每节给出实际命令（以 `personal` 边界为例）与预期输出：
1. **目录与端口**：§12.2 目录表；端口 8918（API，回环）、8928（Worker，回环）、5476（Gateway，回环）；SSM 转发映射 `localhost:18918 → 8918`、`localhost:15476 → 5476`。
2. **首次启动**：`sudo deploy/install/al2023.sh personal` → 以 `crew-personal` 登录各工具（命令在 Task 16 实测后补全）→ `sudo systemctl start octopus-gateway@personal octopus-worker@personal octopus-api` → `sudo -u octopus /opt/kirocrew-on/venv/bin/python -m octopus --config /etc/kirocrew-on/platform.toml init --username admin` → 注册 Worker（`POST /v1/workers`，连接信息使用 `boundary-personal.toml` 的 Worker 地址与 token 文件）。
3. **访问**：`aws ssm start-session --target <实例 ID> --document-name AWS-StartPortForwardingSession --parameters portNumber=8918,localPortNumber=18918`（原生入口同理 5476→15476）。
4. **升级**：`octopus backup` → 拉取新版本 → 重新运行安装脚本（新 release 目录并存）→ 依次重启 api / worker / gateway → `wait-ready`；迁移结果查看启动日志。
5. **回滚**：`ln -sfn /opt/kirocrew-on/releases/<旧版本> /opt/kirocrew-on/current` → 重启；若新版本执行过 schema 迁移，用升级前备份在独立目录 `restore-verify` 后再替换，且记录回滚原因与已执行的代码变更。
6. **重启与停止**：先确认无在途派发（控制台或 `GET /v1/runs`），`systemctl stop` 发送 SIGTERM；禁止 `pkill` 全局结束进程。
7. **日志**：`journalctl -u octopus-api -u octopus-worker@personal -u octopus-gateway@personal`；路由日志 `/var/lib/octopus/boundaries/personal/provider-routing.jsonl`。
8. **认证过期**：现象（任务进入等待 / `auth` 失败类型）→ 以 `crew-personal` 重新登录对应工具 → 不重启平台，Session 会继续。
9. **停止失败**：原生仍有运行或待批准项时保持停止中；检查 `/api/approvals` 与原生任务状态的方法。
10. **磁盘**：Workspace 与日志占用检查、清理边界（只清理已关闭且已备份的 Session）。
11. **队列异常**：大量 Session 停留在同一状态时的检查顺序（Worker 健康、Gateway 就绪、路由日志、退避中的 Session）。
12. **备份恢复**：`octopus backup --dest …`、`octopus archive /var/lib/octopus/boundaries/personal --dest … --name boundary-personal --exclude 'home/.cache/*'`、`octopus restore-verify … --into /tmp/restore-<日期>`；列明未覆盖内容（各工具私有会话目录、远端仓库状态）。

尚未在 EC2 实测的命令一律标注“待 Task 18–19 实测”。

- [ ] **Step 2: 运行回归并提交（确认后）**

Run: `tools/regress.sh` → `regress: OK`

```bash
git add docs/OPERATIONS.md README.md
git commit -m "docs: 运维手册初版（EC2 实测项已标注）"
```

---

# Part A′：本地端到端（macOS，真实 ACP）

负责人要求：**先在本地跑通，再部署到远端**。Part A′ 用本仓库代码在本机拉起完整三服务，使用独立端口
（Gateway 6476 / Worker 8938 / API 8948）与独立数据目录 `state/local-e2e/`（含独立 `KIROCREW_HOME` 与 `KIRO_HOME`），
不读写 `~/.kiro`、不影响已运行的 8916 / 8917 / 8918 / 8928 与 5476。已核实：kiro-cli 的登录态不依赖 `KIRO_HOME`，
Agent 定义从 `KIRO_HOME/agents` 读取；Claude Code 与 Codex 使用各自 HOME 下的现有认证。
真实 ACP 运行会消耗各工具账号的模型额度；测试项目一律新建，不触碰任何历史 Session。

### Task 13: 本地三服务拉起与冒烟

**Files:**
- Create: `tools/local_stack.sh`、`tests/test_local_stack.py`

**Interfaces:**
- Consumes: `composition.gateway`（Task 5）、`octopus serve / worker / wait-ready`（Task 9）、`Settings`（Task 4）。
- Produces: `tools/local_stack.sh init|start|stop|status|restart <service>`；`LOCAL_E2E_DIR`（默认 `state/local-e2e`）下的 `platform.toml`、`boundary.toml`、`logs/`、`run/*.pid`。

- [ ] **Step 1: 写失败测试**

`tests/test_local_stack.py`:

```python
"""Local stack helper: isolated configs and ports, never the user's ~/.kiro."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from octopus.config import load

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools/local_stack.sh"


class LocalStackTests(unittest.TestCase):
    def run_script(self, base, *args):
        env = {**os.environ, "LOCAL_E2E_DIR": str(base)}
        return subprocess.run(["bash", str(SCRIPT), *args], cwd=ROOT, env=env, capture_output=True, text=True)

    def test_init_writes_isolated_configs(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "e2e"
            result = self.run_script(base, "init")
            self.assertEqual(result.returncode, 0, result.stderr)
            boundary = load(base / "boundary.toml", environ={"HOME": str(Path.home())}, cwd=ROOT)
            platform = load(base / "platform.toml", environ={"HOME": str(Path.home())}, cwd=ROOT)
            self.assertEqual((boundary.gateway_port, boundary.worker_port, platform.api_port), (6476, 8938, 8948))
            self.assertEqual(boundary.crew_home, base / "crew")
            self.assertEqual(boundary.kiro_home, base / "kiro")
            self.assertEqual(boundary.worker_data_dir, base / "worker")
            self.assertEqual(platform.data_dir, base / "platform")
            for path in (boundary.crew_home, boundary.kiro_home):
                self.assertFalse(str(path).startswith(str(Path.home() / ".kiro")))

    def test_init_keeps_existing_configs(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "e2e"
            self.run_script(base, "init")
            (base / "platform.toml").write_text((base / "platform.toml").read_text() + "# kept\n")
            self.run_script(base, "init")
            self.assertTrue((base / "platform.toml").read_text().endswith("# kept\n"))

    def test_status_reports_stopped_services(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_script(Path(temporary) / "e2e", "status")
            self.assertEqual(result.returncode, 0, result.stderr)
            for service in ("gateway", "worker", "api"):
                self.assertIn(f"{service}: stopped", result.stdout)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m unittest tests/test_local_stack.py -v`
Expected: FAIL（脚本不存在，returncode 127 / 断言失败）

- [ ] **Step 3: 实现 `tools/local_stack.sh`**

```bash
#!/usr/bin/env bash
# Local end-to-end stack from this checkout on isolated ports and data; never touches ~/.kiro.
#   tools/local_stack.sh init|start|stop|status|restart <gateway|worker|api>
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
BASE="${LOCAL_E2E_DIR:-$ROOT/state/local-e2e}"
RUN="$BASE/run"
LOGS="$BASE/logs"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
KC_PY="${KIROCREW_PYTHON:-/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12}"
SERVICES=(gateway worker api)

init() {
  mkdir -p "$RUN" "$LOGS" "$BASE/worker" "$BASE/crew" "$BASE/kiro" "$BASE/platform"
  chmod 700 "$BASE"
  local kiro_cli
  kiro_cli="$(command -v kiro-cli || true)"
  if [ ! -f "$BASE/boundary.toml" ]; then
    cat > "$BASE/boundary.toml" <<EOF
worker_data_dir = "$BASE/worker"
worker_token_file = "$BASE/worker.token"
crew_home = "$BASE/crew"
kiro_home = "$BASE/kiro"
routing_log = "$BASE/provider-routing.jsonl"
gateway_runtime_file = "$BASE/gateway-runtime.json"
log_dir = "$LOGS"
worker_port = 8938
gateway_port = 6476
worker_isolation = "trusted-local"
crew_url = "http://localhost:6476"
kirocrew_python = "$KC_PY"
adapters_dir = "$ROOT/node_modules/.bin"
path_prepend = ["$HOME/.local/bin", "/opt/homebrew/bin"]

[tools]
claude = "$HOME/.local/bin/claude"
codex = "$ROOT/node_modules/.bin/codex"
opencode = "/opt/homebrew/bin/opencode"
EOF
    if [ -n "$kiro_cli" ]; then echo "kiro_cli = \"$kiro_cli\"" >> "$BASE/boundary.toml"; fi
  fi
  if [ ! -f "$BASE/platform.toml" ]; then
    cat > "$BASE/platform.toml" <<EOF
data_dir = "$BASE/platform"
log_dir = "$LOGS"
api_port = 8948
engine_workers = 4
retry_unavailable = 2.0
EOF
  fi
}

pid_of() {
  local file="$RUN/$1.pid" pid
  [ -f "$file" ] || return 1
  pid="$(cat "$file")"
  # Only our own process: the command line must reference this stack's directory.
  ps -p "$pid" -o command= 2>/dev/null | grep -q -- "$BASE" && echo "$pid"
}

launch() {
  local service="$1"
  if pid_of "$service" >/dev/null; then echo "$service: already running"; return; fi
  case "$service" in
    gateway) env PYTHONPATH="$ROOT" nohup "$KC_PY" -s -m composition.gateway --config "$BASE/boundary.toml" >"$LOGS/gateway.log" 2>&1 & ;;
    worker)  nohup "$PY" -m octopus --config "$BASE/boundary.toml" worker >"$LOGS/worker.log" 2>&1 & ;;
    api)     nohup "$PY" -m octopus --config "$BASE/platform.toml" serve >"$LOGS/api.log" 2>&1 & ;;
  esac
  echo $! > "$RUN/$service.pid"
  case "$service" in
    gateway) "$PY" -m octopus --config "$BASE/boundary.toml" wait-ready --gateway --timeout 90 ;;
    worker)  "$PY" -m octopus --config "$BASE/boundary.toml" wait-ready --worker --timeout 60 ;;
    api)     "$PY" -m octopus --config "$BASE/platform.toml" wait-ready --api --timeout 60 ;;
  esac
  echo "$service: started"
}

halt() {
  local service="$1" pid
  pid="$(pid_of "$service" || true)"
  if [ -z "$pid" ]; then echo "$service: stopped"; rm -f "$RUN/$service.pid"; return; fi
  kill -TERM "$pid"
  for _ in $(seq 1 60); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  if kill -0 "$pid" 2>/dev/null; then echo "$service: still running after SIGTERM (pid $pid)" >&2; return 1; fi
  rm -f "$RUN/$service.pid"
  echo "$service: stopped"
}

case "${1:-}" in
  init) init ;;
  start) init; for s in "${SERVICES[@]}"; do launch "$s"; done ;;
  stop) for ((i=${#SERVICES[@]}-1; i>=0; i--)); do halt "${SERVICES[$i]}"; done ;;
  status) for s in "${SERVICES[@]}"; do if pid_of "$s" >/dev/null; then echo "$s: running"; else echo "$s: stopped"; fi; done ;;
  restart) halt "${2:?service}"; launch "$2" ;;
  *) echo "用法: $0 init|start|stop|status|restart <gateway|worker|api>" >&2; exit 2 ;;
esac
```

（`[tools]` 中的 `kiro_cli` 只在找到 `kiro-cli` 时追加。）

```bash
chmod +x tools/local_stack.sh
```

- [ ] **Step 4: 运行测试与全量回归**

Run: `.venv/bin/python -m unittest tests/test_local_stack.py -v && tools/regress.sh`
Expected: 3 tests OK；`regress: OK`

- [ ] **Step 5: 提交**

```bash
git add tools/local_stack.sh tests/test_local_stack.py
git commit -m "feat: 本地端到端三服务脚本，独立端口与数据目录，不触碰 ~/.kiro"
```

- [ ] **Step 6: 安装 ACP 适配器**

Run: `npm ci && ls node_modules/.bin | grep -E '^(claude-agent-acp|codex-acp|codex)$'`
Expected: 三个名字都出现；版本与 `package-lock.json` 一致（claude-agent-acp 0.84.0、codex-acp 1.11.0）。

- [ ] **Step 7: 记录 `~/.kiro` 基线并初始化**

```bash
find ~/.kiro/agents -maxdepth 1 -type f -exec shasum {} + | sort > .superpowers/kiro-agents-before.txt
tools/local_stack.sh init
```

Expected: `state/local-e2e/` 下生成 `boundary.toml`、`platform.toml`。

- [ ] **Step 8: 组合检查**

Run: `env PYTHONPATH="$PWD" /Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12 -s -m composition.gateway --config state/local-e2e/boundary.toml --check`
Expected: 5 行，依次 `poc-coordinator … kiro`、`poc-kiro … kiro`、`poc-claude … claude`、`poc-codex … codex`、`poc-opencode … opencode`。失败时用 superpowers:systematic-debugging 定位，不改断言。

- [ ] **Step 9: 启动三服务**

Run: `tools/local_stack.sh start && tools/local_stack.sh status`
Expected: 三个 `started`，随后三行 `running`；`state/local-e2e/gateway-runtime.json` 的 `composition` 为 `PocProviderRegistry`。

- [ ] **Step 10: 初始化管理员、登录、注册 Worker**

```bash
umask 077
python3 -c 'import secrets;print(secrets.token_urlsafe(24))' > state/local-e2e/admin.password
OCTOPUS_ADMIN_PASSWORD="$(cat state/local-e2e/admin.password)" .venv/bin/python -m octopus --config state/local-e2e/platform.toml init --username admin --password-env OCTOPUS_ADMIN_PASSWORD
printf '{"username":"admin","password":"%s"}' "$(cat state/local-e2e/admin.password)" > state/local-e2e/login.json
.venv/bin/python -m octopus --config state/local-e2e/platform.toml request POST /v1/login --body-file state/local-e2e/login.json > state/local-e2e/login.out
export OCTOPUS_API_TOKEN="$(python3 -c 'import json;print(json.load(open("state/local-e2e/login.out"))["token"])')"
ADMIN_ID="$(.venv/bin/python -m octopus --config state/local-e2e/platform.toml request GET /v1/me | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')"
printf '{"name":"local-e2e","owner_id":"%s","connection":{"url":"http://127.0.0.1:8938","token":"%s"}}' "$ADMIN_ID" "$(cat state/local-e2e/worker.token)" > state/local-e2e/worker.json
.venv/bin/python -m octopus --config state/local-e2e/platform.toml request POST /v1/workers --body-file state/local-e2e/worker.json
```

Expected: 注册返回 `id` 与 `capabilities.isolation = trusted-local`。（本机同一宿主账号仅允许管理员个人空间使用 trusted-local，这是现有注册规则。）凭据文件只在 `state/local-e2e/`（0600，git 忽略），不打印 token。

- [ ] **Step 11: Kiro 冒烟**

提交单阶段新项目任务（`source.type = new`；`steps = [{"id":"smoke","name":"冒烟","tool":"kiro","model":"auto","effort":"","prompt":"在当前项目创建 HELLO.md，写一句问候；按研发契约写 handoff.md 与 outcome.json"}]`，`worker_id` 为上一步返回值，带唯一 `request_id`），然后 `octopus events <run_id> --once` 直到 `run.completed`。
Expected: Run `completed`；`state/local-e2e/provider-routing.jsonl` 中有该任务的 `actual_backend: "kiro"`。

- [ ] **Step 12: 确认未触碰 `~/.kiro`，停止服务**

```bash
find ~/.kiro/agents -maxdepth 1 -type f -exec shasum {} + | sort | diff .superpowers/kiro-agents-before.txt - && echo "~/.kiro/agents unchanged"
tools/local_stack.sh stop
```

Expected: `~/.kiro/agents unchanged`；三个 `stopped`（SIGTERM 内正常退出）。把 Step 8–12 的实际输出摘要写入台账。

### Task 14: 本地真实研发、返工、重启恢复与备份验收

**Files:**
- Create: `docs/handover/M1-LOCAL-EVIDENCE.md`
- Modify: `docs/MVP-RELEASE.md`（M1 本地验收记录）

**Interfaces:**
- Consumes: Task 13 的本地三服务、管理员 token 与 Worker ID。

- [ ] **Step 1: 最小研发（Claude 编码 → Codex 审查）**：`tools/local_stack.sh start`；新项目任务，两阶段：`code`（claude，提示：用 Node ES module 实现 `add(a, b)` 与 `node --test` 测试并运行）、`review`（codex，提示：读取真实 diff 与测试输出，独立复测）。Expected：两阶段 `accepted`，Run `completed`；证据含实际后端、原生 ID、diff、测试输出。
- [ ] **Step 2: 返工**：新 Run，`code` 提示只要求正数加法，`review` 提示要求覆盖负数与非整数输入并以 `changes_requested` 报告缺口。Expected：`recovery.started` → Coordinator `rework` → `code` Attempt 2 → `review` Attempt 2 `pass`；同一 Run、父会话、Workspace。
- [ ] **Step 3: 含 Kiro 规划阶段**：`plan`（kiro）→ `code`（claude）→ `review`（codex）。Expected：三阶段 `accepted`，路由日志 `actual_backend` 依次为 kiro / claude / codex。
- [ ] **Step 4: OpenCode 单独核实**：单阶段 `deliver`（opencode，`model=auto`）。记录成功或失败的原始现象；失败时在证据中写明用户可见的替换配置（deliver 改用 claude）。不把失败写成通过。
- [ ] **Step 5: 重启恢复**：提交一个两阶段 Run，在 `code` 运行中依次 `tools/local_stack.sh restart api`、`restart worker`、`restart gateway`。Expected：每次重启后 Run 恢复推进或进入有明确原因的状态；同一 Task 不出现重复原生派发（核对 Attempt 数与 `/api/spawn` 中带 `[OCTOPUS-ATTEMPT:<id>]` 标记的任务数）。
- [ ] **Step 6: 并发**：同时提交两个 Run（一个编码任务明显更长）。Expected：短任务不等待长任务完成即推进。
- [ ] **Step 7: 备份恢复**：`.venv/bin/python -m octopus --config state/local-e2e/platform.toml backup --dest state/local-e2e/backups`；`archive state/local-e2e/worker --dest state/local-e2e/backups --name worker`；`restore-verify <manifest> --into state/local-e2e/restore-1`。Expected：`ok: true`，源数据未变。
- [ ] **Step 8: 证据与验收记录**：`docs/handover/M1-LOCAL-EVIDENCE.md` 写脱敏摘要（Run ID、阶段、请求 / 实际模型、实际后端、测试结果、重启观察、未通过项），不含 token、需求以外的用户数据；`docs/MVP-RELEASE.md` §5 增加“本地（macOS）验收”记录。`tools/local_stack.sh stop`；`tools/regress.sh` 全绿后提交：

```bash
git add docs/handover/M1-LOCAL-EVIDENCE.md docs/MVP-RELEASE.md
git commit -m "docs: M1 本地端到端验收记录（真实 ACP）"
```

# Part B：EC2 部署与真实验收

Part B 在 Part A′ 本地验收完成后开始；每个任务开始前**必须**获得负责人对该任务的明确授权，并确认所需外部输入（`docs/MVP-RELEASE.md` §12）。
证据保存在实例 `/var/lib/octopus/evidence/m1/`（不进仓库）；仓库只提交脱敏摘要。

### Task 15: 部署 IaC 与本地校验（已完成，2026-10-11）

D15–D18 确定后实现：`deploy/aws/kirocrew-on-ec2.yaml`（CloudFront → 安全组前缀列表 → EC2，us-east-1）、`deploy/aws/deploy.sh`、
`deploy/aws/stack.env`、`deploy/install/al2023.sh`（KiroCrew 官方源码锁定 commit、kiro-cli 官方清单校验、Bedrock 环境、原生契约后接受版本、
管理员与 Worker 引导）、平台源站认证与 viewer 地址限流、`octopus bootstrap-worker`、`tests/native` 原生契约。
验证：`tests/test_deploy_assets.py`、`tests/test_cloudfront_origin.py`、`tests/test_bootstrap.py` 通过；cfn-lint 与 `validate-template` 通过；
KiroCrew 0.10.0 本机原生契约 6/6、真实冒烟与 Claude→Codex 通过。

### Task 16: 创建栈（授权关口）

**授权关口**：创建云资源（费用估算见执行报告）。确认 `deploy/aws/stack.env` 的候选规格与可选告警邮箱。

- [ ] `deploy/aws/deploy.sh up`；Expected：栈 `CREATE_COMPLETE`，输出 InstanceId、CloudFrontDomain、ConsoleUrl、ArtifactBucket。
- [ ] `deploy/aws/deploy.sh status`；Expected：SSM `Online`（`/health` 此时尚未就绪）。
- [ ] 记录：AMI、实例规格、可用区、前缀列表 ID、`sysctl user.max_user_namespaces`、`unshare -Ur true`（经 SSM 会话执行）。

### Task 17: 发布与安装

- [ ] `deploy/aws/deploy.sh push`；Expected：SSM 命令 `Success`；输出含 KiroCrew 版本、原生契约 6/6、三个服务启用、Worker 注册结果。
  失败时按输出定位（Python / Node 包名、npm 构建、沙箱），修复后提交再 push；不在实例上手工改代码。
- [ ] `deploy/aws/deploy.sh kiro-login`：负责人完成设备码登录。
- [ ] `deploy/aws/deploy.sh status`：`https://<分发域名>/health` 返回 200。

### Task 18: 经 CloudFront 的真实研发与返工

- [ ] 浏览器打开 ConsoleUrl，以管理员登录；SSE 事件流持续更新（经 CloudFront）。
- [ ] 经 `OCTOPUS_URL=https://<分发域名>` 的 CLI 提交：Kiro 冒烟、Claude→Codex、审查返工、Kiro 规划三阶段；OpenCode 单独核实。
  Expected：与本地验收相同的结论；证据含实际后端、原生 ID、diff 与测试输出。
- [ ] 安全核对：直接访问 `http://<EIP>:8918/health` 超时（安全组）；伪造请求缺少 `X-Origin-Verify` 时返回 403；未登记 Host 返回 421。
- [ ] `deploy/aws/deploy.sh tunnel`：原生入口经 SSM 可打开绑定的父会话。

### Task 19: 重启恢复与备份恢复

- [ ] 编码运行中依次重启 `octopus-api`、`octopus-worker@personal`、`octopus-gateway@personal`；Expected：自动恢复、无重复派发。
- [ ] 实例重启（`aws ec2 reboot-instances`）后服务自动启动，Session 继续。
- [ ] `octopus backup` / `archive` / `restore-verify`；DLM 快照在次日出现。

### Task 20: 验收记录

- [ ] **Step 1**：逐条更新 `docs/MVP-RELEASE.md` §5 的 M1 验收复选框：通过的附证据摘要位置，未通过的写原因与恢复条件；§1 状态表更新 M1 状态。
- [ ] **Step 2**：`docs/OPERATIONS.md` 中“待 Task 18–19 实测”的命令替换为实测结果；`deploy/versions.env` 为实测版本。
- [ ] **Step 3**：`tools/regress.sh` 全绿；向负责人报告 M1 结论（含未通过项），确认后提交：

```bash
git add docs deploy/versions.env
git commit -m "docs: M1 EC2 验收记录与运维手册实测更新"
```
