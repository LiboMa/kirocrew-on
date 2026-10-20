"""The record of what was built (owner 2026-10-20): every commit since the base, grouped by its decision (D46 P5a,
D49 第 2 期 …) and marked where it is — pushed, deployed on EC2, or only here — beside the task states kept in
docs/progress.json. Writes the data into docs/progress-dashboard.html and the summary docs/PROGRESS.md.

Run from the repository root:
  python3 tools/progress.py --deployed <the commit EC2 runs> [--pushed origin/feat/m1-runtime-foundation] [--base origin/main]
"""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DATA, DASHBOARD, SUMMARY = ROOT / "docs/progress.json", ROOT / "docs/progress-dashboard.html", ROOT / "docs/PROGRESS.md"
TAG = re.compile(r"D(\d+)(?: (P\d+[a-z]?))?(?: 第 (\d+) 期)?")
KIND = re.compile(r"^(\w+)(?:\(([^)]+)\))?:")
KINDS = {"feat": "功能", "fix": "修复", "docs": "文档", "test": "测试"}
TITLES = {31: "访问管理", 32: "会话修订历史", 33: "Team 与 Worker 管理", 34: "会话变更记录与运行优化", 35: "代码仓库凭证",
          36: "开发成果预览", 37: "候选提交、门禁与报告", 38: "换工具与批准代理", 39: "看门狗", 40: "交接精简与自适应轮询",
          41: "重新规划", 42: "会话预算", 43: "等待与自动恢复", 44: "计划确认", 45: "需求中指定的工具与模型",
          46: "Agent 角色与提示体系", 47: "自动发布 Draft PR", 48: "发布到新仓库", 49: "每人一个执行边界",
          50: "编码技能包", 51: "模型列表与自动发现"}
WHERE = {"pushed": "已推送", "deployed": "已部署 EC2", "local": "仅本机"}
BEGIN, END = "/* DATA:BEGIN (tools/progress.py) */", "/* DATA:END */"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def group_of(subject):
    """The decision a commit belongs to, from its subject: (key, title)."""
    tag = TAG.search(subject)
    if tag:
        number, phase, stage = int(tag.group(1)), tag.group(2), tag.group(3)
        key = f"D{number}" + (f" {phase}" if phase else "") + (f" 第 {stage} 期" if stage else "")
        return key, TITLES.get(number, "")
    kind = KIND.match(subject)   # untagged: by kind only; the scope stays in the subject
    return "未标记 · " + KINDS.get(kind.group(1) if kind else "", "其他"), ""


def ordered(groups):
    """Decisions first, the newest first (then the higher number); the untagged kinds after them, the largest first."""
    tagged = [g for g in groups if not g["key"].startswith("未标记")]
    number = lambda g: int(TAG.match(g["key"]).group(1))
    return (sorted(tagged, key=lambda g: (g["last"], number(g)), reverse=True)
            + sorted((g for g in groups if g["key"].startswith("未标记")), key=lambda g: g["count"], reverse=True))


def where(sha, pushed, deployed):
    return "pushed" if sha in pushed else "deployed" if sha in deployed else "local"


def ancestry(ref):
    return set(git("rev-list", ref).split()) if ref else set()


def record(base, pushed_ref, deployed_ref):
    """Every commit since ``base``, newest first, and the groups they fall in."""
    pushed, deployed = ancestry(pushed_ref), ancestry(deployed_ref)
    commits = []
    for line in git("log", "--format=%H%x1f%h%x1f%ad%x1f%s", "--date=short", f"{base}..HEAD").splitlines():
        sha, short, date, subject = line.split("\x1f", 3)
        key, title = group_of(subject)
        commits.append({"sha": short, "date": date, "subject": subject, "group": key, "title": title,
                        "where": where(sha, pushed, deployed)})
    groups = {}
    for c in commits:
        g = groups.setdefault(c["group"], {"key": c["group"], "title": c["title"], "count": 0, "last": c["date"],
                                           "where": {k: 0 for k in WHERE}})
        g["count"] += 1
        g["where"][c["where"]] += 1
    head = git("rev-parse", "--short", "HEAD").strip()
    return {"head": head, "base": base, "pushed_ref": pushed_ref, "deployed": deployed_ref,
            "deployed_short": git("rev-parse", "--short", deployed_ref).strip() if deployed_ref else None,
            "ahead": sum(1 for c in commits if c["where"] != "pushed"),
            "counts": {k: sum(1 for c in commits if c["where"] == k) for k in WHERE},
            "commits": commits, "groups": ordered(list(groups.values()))}


def completion(items, status):
    return sum(status[i["status"]]["weight"] for i in items) / len(items) if items else 0.0


def markdown(data, rec, generated):
    status, lines = data["status"], []
    scoped = lambda scope: [i for m in data["modules"] if m["scope"] == scope for i in m["items"]]
    lines += ["# 进度与完成记录", "",
              f"> 由 `tools/progress.py` 生成（{generated}）：任务状态取自 `docs/progress.json`，提交记录取自 git。"
              f"看板：`docs/progress-dashboard.html`。", "",
              f"- **本轮完成度**：{completion(scoped('round'), status):.0%}　**MVP 完成度**：{completion(scoped('mvp'), status):.0%}",
              f"- **分支**：HEAD `{rec['head']}`；自 `{rec['base']}` 起 {len(rec['commits'])} 个提交——"
              + "、".join(f"{WHERE[k]} {rec['counts'][k]}" for k in WHERE)
              + (f"；EC2 运行 `{rec['deployed_short']}`" if rec["deployed_short"] else ""),
              f"- **回归**：{data.get('checks', '—')}", "", "## 模块", "",
              "| 模块 | 完成度 | " + " | ".join(s["label"] for s in status.values()) + " |",
              "|---|---|" + "---|" * len(status)]
    for m in data["modules"]:
        lines.append(f"| {m['title']} | {completion(m['items'], status):.0%} | "
                     + " | ".join(str(sum(1 for i in m["items"] if i["status"] == k)) for k in status) + " |")
    lines += ["", "## 下一步", ""] + [f"{n}. {'【需要负责人】' if s['who'] == 'owner' else '【平台】'}{s['text']}"
                                       + (f"：`{s['cmd']}`" if s.get("cmd") else "") for n, s in enumerate(data["next"], 1)]
    lines += ["", "## 本轮修复的缺陷", ""] + [f"- `{sha}` {text}" for sha, text in data["bugs"]]
    lines += ["", "## 完成记录（按决策，最近的在前）", ""]
    for g in rec["groups"]:
        mix = "、".join(f"{WHERE[k]} {n}" for k, n in g["where"].items() if n)
        lines += [f"### {g['key']}{(' · ' + g['title']) if g['title'] else ''}（{g['count']} 个提交：{mix}）", ""]
        lines += [f"- `{c['sha']}` {c['date']} 〔{WHERE[c['where']]}〕 {c['subject']}" for c in rec["commits"] if c["group"] == g["key"]]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def embed(html, payload):
    """The dashboard with its data block replaced (the page opens from disk, so the data travels inside it)."""
    start, end = html.index(BEGIN), html.index(END)
    return html[:start + len(BEGIN)] + "\nconst DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n" + html[end:]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--deployed", help="the commit EC2 runs (readlink /opt/kirocrew-on/current)")
    parser.add_argument("--pushed", default="origin/feat/m1-runtime-foundation")
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args(argv)
    data = json.loads(DATA.read_text())
    rec = record(args.base, args.pushed, args.deployed)
    generated = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    DASHBOARD.write_text(embed(DASHBOARD.read_text(), {**data, "generated": generated, "record": rec}))
    SUMMARY.write_text(markdown(data, rec, generated))
    print(f"{len(rec['commits'])} 个提交（" + "、".join(f"{WHERE[k]} {rec['counts'][k]}" for k in WHERE)
          + f"）→ {DASHBOARD.relative_to(ROOT)}、{SUMMARY.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
