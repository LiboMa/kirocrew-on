"""The repository's reviewed knowledge (D52): a Session's Knowledge items are proposed, when it is published, as a draft
pull / merge request of .octopus/knowledge.md on one fixed branch, which people review and merge — knowledge every member's
Sessions of that repository then read, whatever boundary they run on. Only that file is written, never the default branch,
never by force, through the hosting API with the publish's write credential (not on a Worker)."""
import base64
import datetime
import urllib.parse

FILE, BRANCH = ".octopus/knowledge.md", "octopus/knowledge"
TITLE = "项目知识更新（KiroCrew ON）"
HEAD = ("# 项目知识\n\n> 由 KiroCrew ON 的 Knowledge 任务提出，经 Draft PR 由人审核后合并；修改或删除条目同样经 PR 审核。"
        "任务开始前阅读；与任务契约冲突时以契约为准。\n")
RULE = "- 规则："


def _line(text):
    return " ".join(str(text or "").split())


def render(text, items, rid, day):
    """The file with the items it does not hold yet appended: (new text, the items added). A rule already in the file is
    left out."""
    known = {line[len(RULE):].strip() for line in (text or "").splitlines() if line.startswith(RULE)}
    added, out = [], (text or "").rstrip("\n") or HEAD.rstrip("\n")
    for item in items:
        rule = _line(item.get("rule"))
        if not rule or rule in known:
            continue
        known.add(rule)
        added.append({"rule": rule, **({"avoid": _line(item["avoid"])} if item.get("avoid") else {})})
        out += f"\n\n## {rule[:60]}\n\n{RULE}{rule}\n" + (f"- 避免：{_line(item['avoid'])}\n" if item.get("avoid") else "") \
            + f"- 来源：会话 {rid} · {day}"
    return out + "\n", added


def _decode(answer):
    return base64.b64decode(answer["content"]).decode() if answer and answer.get("content") else ""


def _github(api, base, path, items, rid, day, secret):
    kind = "github_pat"
    default = api("GET", f"{base}/repos/{path}", secret, kind)["default_branch"]
    branch = api("GET", f"{base}/repos/{path}/git/ref/heads/{BRANCH}", secret, kind, missing=True)
    current = api("GET", f"{base}/repos/{path}/contents/{FILE}?" + urllib.parse.urlencode({"ref": BRANCH if branch else default}),
                  secret, kind, missing=True)
    text, added = render(_decode(current), items, rid, day)
    if not added:
        return default, added
    if not branch:   # from the default branch's head: a merged and deleted proposal starts again from what was merged
        head = api("GET", f"{base}/repos/{path}/git/ref/heads/{default}", secret, kind)["object"]["sha"]
        api("POST", f"{base}/repos/{path}/git/refs", secret, kind, {"ref": "refs/heads/" + BRANCH, "sha": head})
    api("PUT", f"{base}/repos/{path}/contents/{FILE}", secret, kind,
        {"message": f"docs(knowledge): {len(added)} 条项目知识（KiroCrew ON 会话 {rid}）", "branch": BRANCH,
         "content": base64.b64encode(text.encode()).decode(), **({"sha": current["sha"]} if current else {})})
    return default, added


def _gitlab(api, base, path, items, rid, day, secret):
    kind, project = "gitlab_token", urllib.parse.quote(path, safe="")
    default = api("GET", f"{base}/projects/{project}", secret, kind)["default_branch"]
    branch = api("GET", f"{base}/projects/{project}/repository/branches/" + urllib.parse.quote(BRANCH, safe=""), secret, kind,
                 missing=True)
    current = api("GET", f"{base}/projects/{project}/repository/files/{urllib.parse.quote(FILE, safe='')}?"
                  + urllib.parse.urlencode({"ref": BRANCH if branch else default}), secret, kind, missing=True)
    text, added = render(_decode(current), items, rid, day)
    if added:
        api("POST", f"{base}/projects/{project}/repository/commits", secret, kind,
            {"branch": BRANCH, **({} if branch else {"start_branch": default}),
             "commit_message": f"docs(knowledge): {len(added)} 条项目知识（KiroCrew ON 会话 {rid}）",
             "actions": [{"action": "update" if current else "create", "file_path": FILE, "content": text, "encoding": "text"}]})
    return default, added


def propose(kind, host, path, items, rid, secret, day=None):
    """Put the items on the proposal branch and open (or reuse) its draft request: {result, url, number, kind, reused,
    items}. ``unchanged`` when the file already holds every item — nothing is written then."""
    from . import credentials as repo_credentials, publish as publishing
    day = day or datetime.date.today().isoformat()
    base = repo_credentials.api_base(kind, host)
    write = _github if kind == "github_pat" else _gitlab
    default, added = write(publishing._api, base, path, items, rid, day, secret)
    if not added:
        return {"result": "unchanged", "items": 0}
    body = "由 KiroCrew ON 的 Knowledge 任务提出，请审核后合并：\n\n" + "\n".join(f"- {i['rule']}" for i in added) \
        + f"\n\n来源：会话 {rid}。合并后，该仓库之后的任务都会阅读 `{FILE}`。"
    request = publishing.pull_request(kind, host, path, BRANCH, default, TITLE, body, secret)
    return {"result": "proposed", "items": len(added), **{k: request[k] for k in ("url", "number", "kind", "reused")}}
