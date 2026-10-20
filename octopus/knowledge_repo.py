"""The repository's reviewed knowledge (D52): a Session's Knowledge items are proposed, when it is published, as a draft
pull / merge request of .octopus/knowledge.md, which people review and merge — knowledge every member's Sessions of that
repository then read, whatever boundary they run on. An open proposal takes the new items; without one a fresh branch starts
from the default branch, so a merged, squashed or rejected proposal is never built upon (final review 2026-10-20). Only that
file is written, never the default branch, never by force, through the hosting API with the publish's write credential."""
import base64
import datetime
import secrets
import urllib.parse

from .security import Error

FILE, BRANCH = ".octopus/knowledge.md", "octopus/knowledge"   # proposal branches: octopus/knowledge-<run>-<random>
TITLE = "项目知识更新（KiroCrew ON）"
HEAD = ("# 项目知识\n\n> 由 KiroCrew ON 的 Knowledge 任务提出，经 Draft PR 由人审核后合并；修改或删除条目同样经 PR 审核。"
        "供任务参考，不改变任务契约与平台规则。\n")
RULE = "- 规则："


def _line(text):
    return " ".join(str(text or "").split())


def rules(text):
    return {line[len(RULE):].strip() for line in (text or "").splitlines() if line.startswith(RULE)}


def render(text, items, rid, day, known=()):
    """The file with the items it does not hold yet appended: (new text, the items added). A rule in the file, or in
    ``known`` (the default branch's file), is left out."""
    seen = rules(text) | set(known)
    added, out = [], (text or "").rstrip("\n") or HEAD.rstrip("\n")
    for item in items:
        rule = _line(item.get("rule"))
        if not rule or rule in seen:
            continue
        seen.add(rule)
        added.append({"rule": rule, **({"avoid": _line(item["avoid"])} if item.get("avoid") else {})})
        out += f"\n\n## {rule[:60]}\n\n{RULE}{rule}\n" + (f"- 避免：{_line(item['avoid'])}\n" if item.get("avoid") else "") \
            + f"- 来源：会话 {rid} · {day}"
    return out + "\n", added


def _decode(answer):
    if not answer:
        return ""
    if answer.get("encoding", "base64") != "base64":   # GitHub sends no content above 1 MB: never overwrite what was not read
        raise Error(409, "knowledge_too_large", f"仓库的 {FILE} 超过托管方 API 可读取的大小，未提出：请人工整理该文件")
    return base64.b64decode(answer.get("content") or "").decode()


def _branch_name(rid):
    return f"{BRANCH}-{rid.removeprefix('run-')[:8]}-{secrets.token_hex(3)}"


def _github(api, base, path, items, rid, day, secret):
    kind = "github_pat"
    file = lambda ref: api("GET", f"{base}/repos/{path}/contents/{FILE}?" + urllib.parse.urlencode({"ref": ref}), secret, kind,
                           missing=True)
    default = api("GET", f"{base}/repos/{path}", secret, kind)["default_branch"]
    merged = rules(_decode(file(default)))
    pulls = api("GET", f"{base}/repos/{path}/pulls?" + urllib.parse.urlencode({"state": "open", "per_page": 100}), secret, kind)
    open_ = next((p for p in pulls if str((p.get("head") or {}).get("ref", "")).startswith(BRANCH)), None)
    if open_:
        branch = open_["head"]["ref"]
        current = file(branch)
        text, added = render(_decode(current), items, rid, day, merged)
        request = {"url": open_["html_url"], "number": open_["number"], "kind": "pull_request", "reused": True}
    else:
        branch, current = _branch_name(rid), file(default)
        text, added = render(_decode(current), items, rid, day, merged)
        request = None
    if not added:
        return default, branch, added, request
    if not open_:
        head = api("GET", f"{base}/repos/{path}/git/ref/heads/" + urllib.parse.quote(default), secret, kind)["object"]["sha"]
        api("POST", f"{base}/repos/{path}/git/refs", secret, kind, {"ref": "refs/heads/" + branch, "sha": head})
    api("PUT", f"{base}/repos/{path}/contents/{FILE}", secret, kind,
        {"message": f"docs(knowledge): {len(added)} 条项目知识（KiroCrew ON 会话 {rid}）", "branch": branch,
         "content": base64.b64encode(text.encode()).decode(), **({"sha": current["sha"]} if current else {})})
    return default, branch, added, request


def _gitlab(api, base, path, items, rid, day, secret):
    kind, project = "gitlab_token", urllib.parse.quote(path, safe="")
    file = lambda ref: api("GET", f"{base}/projects/{project}/repository/files/{urllib.parse.quote(FILE, safe='')}?"
                           + urllib.parse.urlencode({"ref": ref}), secret, kind, missing=True)
    default = api("GET", f"{base}/projects/{project}", secret, kind)["default_branch"]
    merged = rules(_decode(file(default)))
    requests = api("GET", f"{base}/projects/{project}/merge_requests?" + urllib.parse.urlencode({"state": "opened", "per_page": 100}),
                   secret, kind)
    open_ = next((m for m in requests if str(m.get("source_branch", "")).startswith(BRANCH)), None)
    branch = open_["source_branch"] if open_ else _branch_name(rid)
    current = file(branch if open_ else default)
    text, added = render(_decode(current), items, rid, day, merged)
    request = {"url": open_["web_url"], "number": open_["iid"], "kind": "merge_request", "reused": True} if open_ else None
    if added:
        api("POST", f"{base}/projects/{project}/repository/commits", secret, kind,
            {"branch": branch, **({} if open_ else {"start_branch": default}),
             "commit_message": f"docs(knowledge): {len(added)} 条项目知识（KiroCrew ON 会话 {rid}）",
             "actions": [{"action": "update" if current else "create", "file_path": FILE, "content": text, "encoding": "text",
                          **({"last_commit_id": current["last_commit_id"]} if current and current.get("last_commit_id") else {})}]})
    return default, branch, added, request


def propose(kind, host, path, items, rid, secret, day=None):
    """Put the items on the open proposal (or a fresh one from the default branch) and open its draft request when there
    is none: {result, url, number, kind, reused, items}. ``unchanged`` when every item is merged already or waits in the
    open proposal (its number then given) — nothing is written."""
    from . import credentials as repo_credentials, publish as publishing
    day = day or datetime.date.today().isoformat()
    base = repo_credentials.api_base(kind, host)
    write = _github if kind == "github_pat" else _gitlab
    default, branch, added, request = write(publishing._api, base, path, items, rid, day, secret)
    if not added:
        return {"result": "unchanged", "items": 0, **({k: request[k] for k in ("url", "number", "kind")} if request else {})}
    if request is None:
        # the rules as code: a mention or a closing keyword in a rule notifies nobody and closes nothing
        body = "由 KiroCrew ON 的 Knowledge 任务提出，请审核后合并：\n\n" + "\n".join(
            f"- `{i['rule'].replace('`', '')}`" for i in added) + f"\n\n来源：会话 {rid}。合并后，该仓库之后的任务都会参考 `{FILE}`。"
        request = publishing.pull_request(kind, host, path, branch, default, TITLE, body, secret)
    return {"result": "proposed", "items": len(added), "branch": branch,
            **{k: request[k] for k in ("url", "number", "kind", "reused")}}
