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
