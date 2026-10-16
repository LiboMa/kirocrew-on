"""Explainable, side-effect-free workflow matching shared by Web and Chat."""
import copy
import json
from pathlib import Path
import re
import unicodedata


CORE = [
    {"id": "chain", "name": "完整研发链", "description": "分析需求、实现、独立审查与交付。",
     "steps": [
         {"id": "plan", "name": "分析需求与验收", "tool": "kiro", "kind": "design", "acceptance": ["交接中给出实现计划、接口约定与可验证的验收条件"],
          "prompt": "阅读源码与用户需求，形成实现计划、接口约定与可验证的验收条件。此步只做分析与交接，不修改业务代码。"},
         {"id": "code", "name": "实现与基础测试", "tool": "claude", "kind": "implement", "acceptance": ["改动满足本次目标，并在交接中给出实际执行的验证命令与结果"],
          "prompt": "按前序计划实现功能，运行项目相关测试。交接具体修改、命令和测试结果。"},
         {"id": "review", "name": "Security / Testing Review", "tool": "codex", "kind": "review",
          "acceptance": ["逐条核对本次目标与实际改动；需修复的问题以 changes_requested 列出（文件与行）"],
          "prompt": "独立阅读实际源码与 diff，审查安全、边界条件与测试覆盖。运行必要验证，记录问题；需修复的问题给出 changes_requested 并列出，"
                    "缺少输入、认证或权限时才用 BLOCKED。"},
         {"id": "delivery", "name": "交付与开发状态页", "tool": "claude", "kind": "deliver", "acceptance": ["交付物可按交接中的说明运行或查看，并列出通过项与限制"],
          "prompt": "核对前序交接与审查，整理开发说明、Release 文档和本地 HTML 状态页面；明确通过项及限制，运行最终验证。"}]},
    {"id": "proof", "name": "最小实现与审查", "description": "先实现最小功能，再独立验证。",
     "steps": [
         {"id": "code", "name": "最小可用实现", "tool": "claude", "kind": "implement", "acceptance": ["改动满足本次目标，并在交接中给出实际执行的验证命令与结果"],
          "prompt": "以最少依赖实现用户需求。编写并运行真实测试，说明启动方法与交付文件。"},
         {"id": "review", "name": "独立验证", "tool": "codex", "kind": "review",
          "acceptance": ["逐条核对本次目标与实际改动；需修复的问题以 changes_requested 列出（文件与行）"],
          "prompt": "读取前序交接和真实代码，独立运行测试并检查异常输入。明确报告通过项；需修复的问题给出 changes_requested 并列出。"}]},
    {"id": "fix", "name": "缺陷定位与修复", "description": "定位缺陷，完成修复与回归。",
     "steps": [
         {"id": "diagnose", "name": "定位和复现问题", "tool": "kiro", "kind": "design", "acceptance": ["交接中给出复现步骤、实际证据、根因与最小修复方案"],
          "prompt": "依据需求与源码复现缺陷，提供实际证据、根因分析和最小修复方案。"},
         {"id": "fix", "name": "修复与回归", "tool": "codex", "kind": "implement", "acceptance": ["缺陷已修复，并在交接中给出针对性回归验证的命令与结果"],
          "prompt": "依据前序交接修复缺陷，加入针对性回归验证，记录实际命令及结果。"}]},
]

# Reuse the existing template library, including examples and task contracts.
LEGACY = Path(__file__).resolve().parent / "templates" / "workflow-templates.json"
SIGNALS = {
    "bugfix": ["bug", "修复", "缺陷", "报错", "崩溃", "闪退", "debug", "fix"],
    "review": ["审查", "审计", "review", "audit", "安全检查"],
    "idea-validation": ["只做规划", "只交付文档", "需求分析", "验收清单", "不编码", "产品方案"],
    "product-landing": ["落地页", "landing", "官网", "产品介绍页"],
    "team-kanban": ["看板", "kanban", "任务管理", "拖拽任务"],
    "csv-insights": ["csv", "数据概览", "数据可视化"],
    "python-cli": ["python cli", "python 命令行", "python命令行"],
    "node-api": ["node api", "node.js api", "nodejs api", "node.js 接口"],
    "go-web": ["go api", "golang", "go 后端", "go web"],
    "web-delivery": ["web应用", "web 应用", "前后端", "web app"],
    "proof": ["最小", "快速验证", "验证想法", "小函数", "poc"],
    "chain": ["完整研发", "完整流程", "全流程"],
}


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def contains(value, term):
    if re.fullmatch(r"[a-z0-9 ]+", term):
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", value) is not None
    return term in value


def catalog():
    rows = copy.deepcopy(CORE) + json.loads(LEGACY.read_text())
    for row in rows:
        row["steps"] = [{"model": "auto", "effort": "", **step} for step in row["steps"]]
    return rows


def ranked(intent, workflows):
    """Rank only workflows already filtered through the caller's permissions."""
    query = normalized(intent)
    found = {key: [term for term in terms if contains(query, term)] for key, terms in SIGNALS.items()}
    rows = []
    for template in catalog():
        hits = found.get(template["id"], [])
        score = len(hits) * 20
        if normalized(template["name"]) in query:
            score += 500
        if template["id"] == "chain":
            score += 1  # Explicit fallback; never labelled a semantic/LLM match.
        rows.append({**template, "kind": "template", "score": score,
                     "reason": "匹配词：" + "、".join(hits) if hits else "通用研发流程，可在任务列表中调整"})
    for flow in workflows:
        if flow["role"] == "viewer":
            continue
        title = normalized(flow["name"])
        named = title in query or flow["id"] in query
        content = normalized(flow["name"] + " " + " ".join(s["name"] + " " + s["prompt"] for s in flow["steps"]))
        hits = sorted({term for terms in found.values() for term in terms if contains(content, term)})
        # Specific user workflows win matching categories; generic task text does not
        # outrank a more specific template without matching intent vocabulary.
        score = 1000 if named else len(hits) * 20 + (5 if hits else 0)
        if score:
            rows.append({"kind": "workflow", "id": flow["id"], "name": flow["name"], "version": flow["version"],
                         "steps": flow["steps"], "score": score,
                         "reason": "需求中指定了工作流名称或 ID" if named else "已有工作流匹配：" + "、".join(hits)})
    return sorted(rows, key=lambda row: -row["score"])


def selection(row):
    return ({"workflow_id": row["id"], "workflow_version": row["version"]}
            if row["kind"] == "workflow" else {"template_id": row["id"]})


def preset_steps(row, goal):
    """Template-specific prompts include the current goal; manual steps are untouched."""
    result = copy.deepcopy(row["steps"])
    for index, step in enumerate(result):
        # the goal is the instructions' own heading (本次研发任务): not repeated in each step (D46)
        step["prompt"] += (f"\n\n当前步骤：{index + 1}/{len(result)} · {step['name']}。"
                           "\n结合本项目的实际源码和前序交接执行，交付可核查的结果与验证记录。")
    return result


def title_from(intent):
    return re.split(r"[\n。！？!?]", intent, maxsplit=1)[0].strip()[:60] or "研发任务"
