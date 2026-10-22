# 发布时新建仓库（D48）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建源码的会话可以发布到一个新建（或确认为空）的仓库：平台用发布者指定的写凭证建仓库，基线推到 `main`，工作推到 `octopus/<run>` 并建 Draft PR。

**Architecture:** `octopus/publish.py` 增加托管方的仓库操作（查、建、空仓库的基线推送）；`Service.publish` 通过新的 `_publish_target` 决定发布目标——git 会话照旧，新建源码的会话用请求中的 `repository` 与 `credential`，首次发布后记为 `snapshot.publish_target`，此后再次发布、自动发布都沿用它。规划者只给出建议（`publish_suggestion`），不自动创建。控制台、MCP、Skill 与 OpenAPI 同步。

**Tech Stack:** Python 3.12 标准库（urllib、subprocess git）、unittest；React + TypeScript + vitest。

**Spec:** `docs/superpowers/specs/2026-10-17-publish-new-repository-design.md`（负责人 2026-10-17 审阅通过）

## Global Constraints

- 只用于 `source.type = new` 的会话；git 会话的发布行为与消息不变。
- `repository` 为 `{owner, name, private?}`，`private` 默认 true；owner / name 只接受 `[A-Za-z0-9_][A-Za-z0-9_.-]*`，name ≤ 100、不以 `.git` 结尾、不含 `..`。
- 凭证：写权限；`repos` 范围覆盖 `owner/name`；按 `credentials.check` 核对发布者可用。缺建仓库权限时 403 说明需要 classic PAT `repo` / 细粒度 Administration 写 / GitLab `api`。
- GitHub：`owner` 等于 `GET /user` 的 `login`（不分大小写）时 `POST /user/repos`，否则 `POST /orgs/{owner}/repos`；`auto_init: false`。GitLab：`GET /namespaces/{owner}` 取 id，`POST /projects`（`visibility` private/public，`initialize_with_readme: false`）。
- 基线只推到空仓库（`ls-remote --heads` 无输出）的 `main`；有任何分支的仓库拒绝（409 `repository_not_empty`），绝不写已有仓库的默认分支。
- 新仓库只建一次：建好即记 `publish_target`；之后的发布与自动发布沿用，不能改到另一个仓库（409 `publish_target_fixed`）。
- 规划者识别到建仓库只记 `publish_suggestion` 与说明，不开启自动发布、不创建；自动创建只在用户启动时（或会话页）自己给出 `publish.repository` 与 `publish.credential`。
- 每次建仓库写审计 `credential.use`（`action: repo.create`，结果）；时间线 `run.repository`（标题「已新建仓库」）。
- 写凭证不进入执行环境；只推已验证候选；秘密扫描、只快进、不 force push 与现有发布相同。
- 每个任务结束跑 `PYTHON=<venv python> bash tools/regress.sh` 的相应部分；任务全部完成后跑完整的 `tools/regress.sh`。

## Review Focus

- 建仓库成功但随后推送失败：再次发布不重建、不重复推基线、沿用该仓库（Task 2 `test_a_publish_that_failed_after_the_repository_was_made_continues_there`）。
- 同名仓库已存在且有内容：不推送、不建 PR、不记 `publish_target`（Task 2 `test_an_existing_repository_with_content_is_left_alone`）。
- 凭证范围不覆盖新仓库路径（如 `repos: ["other/*"]`）：发布前拒绝，不调用托管方（Task 2 `test_a_credential_that_does_not_cover_the_new_repository_is_refused_before_any_call`）。
- 自动发布的新建源码会话启动时未给仓库：会话页开启自动发布时拒绝并说明（Task 2 `test_turning_on_the_automatic_publish_needs_a_repository_for_a_new_source`）。
- 规划者给出的仓库名不合法：计划照常应用，建议被丢弃并记说明（Task 3 `test_an_invalid_suggested_repository_is_dropped_with_a_note`）。

---

### Task 1: 托管方的仓库操作（publish.py）

**Files:**
- Modify: `octopus/publish.py`
- Modify: `tests/test_publish.py`（FakeApi 增加仓库路由，默认行为不变）
- Create: `tests/test_publish_new_repo.py`

**Interfaces:**
- Produces:
  - `publish.check_repository(value) -> {"owner": str, "name": str, "private": bool}`（不合法 `Error` 400）
  - `publish.find_repository(kind, host, path, secret) -> dict | None`
  - `publish.create_repository(kind, host, repository, secret) -> str`（新仓库的网页地址）
  - `publish.push_baseline(workdir, url, base, auth) -> None`（非空 409 `repository_not_empty`）
  - `publish._api(method, url, secret, kind, body=None, *, missing=False, need=PULL_NEED)`；常量 `PULL_NEED`、`CREATE_NEED`
- FakeApi（测试）：`FakeApi.repos`（None = 旧行为：一切仓库都存在）、`FakeApi.made`（建仓库请求 `(path, body)`）、`FakeApi.login`（`GET /user` 的 login，默认 `"owner"`）。

- [ ] **Step 1: 扩展 FakeApi（tests/test_publish.py）**

在 `class FakeApi` 中：类属性改为
```python
    pulls, created, seen, refuse, repos, made, login = [], [], [], None, None, [], "owner"
```
在 `do_GET` 的 `path.endswith("/pulls")` 判断之前加入：
```python
        if path == "/user" or path == "/api/v4/user":
            return self.answer(200, {"login": FakeApi.login, "username": FakeApi.login})
        if path.startswith("/api/v4/namespaces/"):
            return self.answer(200, {"id": 7, "full_path": urllib.parse.unquote(path.rsplit("/", 1)[1])})
        if FakeApi.repos is not None and (path.startswith("/repos/") or path.startswith("/api/v4/projects/")) \
                and not path.endswith(("/pulls", "/merge_requests")):
            name = path[len("/repos/"):] if path.startswith("/repos/") else urllib.parse.unquote(path[len("/api/v4/projects/"):])
            if name not in FakeApi.repos:
                return self.answer(404, {"message": "Not Found"})
```
在 `do_POST` 的 `FakeApi.created.append(body)` 之前加入：
```python
        if self.path in ("/user/repos", "/api/v4/projects") or (self.path.startswith("/orgs/") and self.path.endswith("/repos")):
            owner = (FakeApi.login if self.path == "/user/repos" else self.path.split("/")[2] if self.path.startswith("/orgs/")
                     else FakeApi.namespace)
            name = f"{owner}/{body.get('name') or body.get('path')}"
            bare = GitHost.root / (name + ".git")
            bare.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
            subprocess.run(["git", "-C", str(bare), "config", "http.receivepack", "true"], check=True)
            FakeApi.repos = (FakeApi.repos or set()) | {name}
            FakeApi.made.append((self.path, body))
            return self.answer(201, {"html_url": f"https://forge.example/{name}", "web_url": f"https://forge.example/{name}",
                                     "full_name": name, "path_with_namespace": name})
```
并在 namespace 路由里记下 `FakeApi.namespace = <owner>`（类属性 `namespace = None`）：把上面的 namespaces 分支改为
```python
        if path.startswith("/api/v4/namespaces/"):
            FakeApi.namespace = urllib.parse.unquote(path.rsplit("/", 1)[1])
            return self.answer(200, {"id": 7, "full_path": FakeApi.namespace})
```
在 `PublishCase.setUp` 的重置行改为：
```python
        FakeApi.pulls, FakeApi.created, FakeApi.seen, FakeApi.refuse = [], [], [], None
        FakeApi.repos, FakeApi.made, FakeApi.login, FakeApi.namespace = None, [], "owner", None
```

- [ ] **Step 2: 写失败的测试（tests/test_publish_new_repo.py）**

```python
"""D48: a new-source Session publishes to a repository the platform creates (or one that is empty): the baseline becomes
`main`, the work arrives on octopus/<run> as a Draft PR. The host side here; the Session side in later classes."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from octopus import publish
from octopus.security import Error
from tests.test_private_clone import GitHost
from tests.test_publish import SECRET, FakeApi, PublishCase


class RepositoryApiTests(PublishCase):
    def setUp(self):
        super().setUp()
        FakeApi.repos = {"owner/app"}

    def test_a_repository_of_the_credentials_own_account_is_made_under_the_user(self):
        url = publish.create_repository("github_pat", self.host, {"owner": "Owner", "name": "fresh", "private": True}, SECRET)
        self.assertEqual(url, "https://forge.example/owner/fresh")
        where, body = FakeApi.made[0]
        self.assertEqual((where, body["name"], body["private"], body["auto_init"]), ("/user/repos", "fresh", True, False))

    def test_an_organisations_repository_is_made_under_the_organisation(self):
        publish.create_repository("github_pat", self.host, {"owner": "acme", "name": "tool", "private": False}, SECRET)
        self.assertEqual((FakeApi.made[0][0], FakeApi.made[0][1]["private"]), ("/orgs/acme/repos", False))

    def test_gitlab_makes_a_project_in_the_namespace(self):
        publish.create_repository("gitlab_token", self.host, {"owner": "group", "name": "svc", "private": True}, SECRET)
        where, body = FakeApi.made[0]
        self.assertEqual((where, body["namespace_id"], body["visibility"], body["initialize_with_readme"]),
                         ("/api/v4/projects", 7, "private", False))

    def test_a_repository_is_found_or_not(self):
        self.assertIsNotNone(publish.find_repository("github_pat", self.host, "owner/app", SECRET))
        self.assertIsNone(publish.find_repository("github_pat", self.host, "owner/none", SECRET))

    def test_a_credential_that_may_not_create_says_what_it_needs(self):
        FakeApi.refuse = 403
        with self.assertRaises(Error) as caught:
            publish.create_repository("github_pat", self.host, {"owner": "owner", "name": "fresh", "private": True}, SECRET)
        self.assertEqual(caught.exception.code, "credential_permission")
        self.assertIn("Administration", str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))

    def test_names_are_checked(self):
        for bad in ({"owner": "a/b", "name": "x"}, {"owner": "a", "name": "../x"}, {"owner": "a", "name": "x.git"},
                    {"owner": "a", "name": ""}, {"owner": "a", "name": "x" * 101}, {"owner": "-a", "name": "x"},
                    {"owner": "a", "name": "x", "private": "yes"}, {"owner": "a", "name": "x", "extra": 1}, "a/x"):
            with self.subTest(bad), self.assertRaises(Error):
                publish.check_repository(bad)
        self.assertEqual(publish.check_repository({"owner": "LiboMa", "name": "repo-create_test.1"}),
                         {"owner": "LiboMa", "name": "repo-create_test.1", "private": True})

    def test_main_starts_at_the_baseline_only_in_an_empty_repository(self):
        publish.create_repository("github_pat", self.host, {"owner": "owner", "name": "fresh", "private": True}, SECRET)
        work = Path(tempfile.mkdtemp(dir=self.tmp.name))
        subprocess.run(["git", "init", "-q", str(work)], check=True)
        subprocess.run(["git", "-C", str(work), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty",
                        "-m", "base"], check=True)
        base = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        auth = {"kind": "github_pat", "host": self.host, "username": "x-access-token", "secret": SECRET}
        url = f"https://{self.host}/owner/fresh.git"
        publish.push_baseline(work, url, base, auth)
        main = subprocess.run(["git", "-C", str(GitHost.root / "owner/fresh.git"), "rev-parse", "refs/heads/main"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(main, base)
        with self.assertRaises(Error) as caught:
            publish.push_baseline(work, url, base, auth)
        self.assertEqual(caught.exception.code, "repository_not_empty")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行，确认失败**

Run: `.venv/bin/python -m unittest tests.test_publish_new_repo -v`
Expected: ERROR `AttributeError: module 'octopus.publish' has no attribute 'create_repository'`（及 `check_repository` 等）。

- [ ] **Step 4: 实现（octopus/publish.py）**

模块文档字符串末尾加一句：
```
D48: a Session started from a new project publishes to a repository the platform creates (or one that is empty): its
baseline becomes `main`, and the work arrives on its octopus/… branch as a draft request like any other publish.
```
常量（`LIMIT` 之后）：
```python
SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
PULL_NEED = "GitHub 细粒度令牌需对该仓库有 Pull requests 读写权限，GitLab 令牌需 api 范围"
CREATE_NEED = ("GitHub classic PAT 需 repo 范围，或细粒度令牌对该账户 / 组织有 Administration 读写权限；"
               "GitLab 令牌需 api 范围")


def check_repository(value):
    """A new repository to publish a Session to (D48 R1): {owner, name, private?} — private unless said otherwise."""
    require(isinstance(value, dict) and set(value) <= {"owner", "name", "private"}, "repository 须为 {owner, name, private?}")
    owner, name, private = value.get("owner"), value.get("name"), value.get("private", True)
    require(isinstance(owner, str) and len(owner) <= 100 and SEGMENT.fullmatch(owner) and ".." not in owner,
            "repository.owner 只能含字母、数字、. _ -（以字母、数字或 _ 开头）")
    require(isinstance(name, str) and len(name) <= 100 and SEGMENT.fullmatch(name) and ".." not in name
            and not name.lower().endswith(".git"), "repository.name 只能含字母、数字、. _ -（1–100 个字符，不以 .git 结尾）")
    require(isinstance(private, bool), "repository.private 须为 true 或 false")
    return {"owner": owner, "name": name, "private": private}
```
`_api` 的签名与 403 分支改为（其余不变）：
```python
def _api(method, url, secret, kind, body=None, *, missing=False, need=PULL_NEED):
```
```python
        if exc.code == 404 and missing:
            return None
        if exc.code == 401:
            raise Error(409, "credential_rejected", "托管方拒绝了凭证（HTTP 401）") from None
        if exc.code == 403:   # a valid token without this permission (or the host's rate limit)
            raise Error(409, "credential_permission", sources.scrub(
                f"凭证没有此操作的权限（HTTP 403：{detail[:200]}）：{need}", secret)) from None
```
文件末尾增加：
```python
def find_repository(kind, host, path, secret):
    """The repository at ``path``, or None when it does not exist (or the credential cannot see it)."""
    base = repo_credentials.api_base(kind, host)
    if kind == "github_pat":
        return _api("GET", f"{base}/repos/{path}", secret, kind, missing=True)
    return _api("GET", f"{base}/projects/{urllib.parse.quote(path, safe='')}", secret, kind, missing=True)


def create_repository(kind, host, repository, secret):
    """Create an empty repository (D48 R3) — no README or licence — and return its web address."""
    base, owner, name = repo_credentials.api_base(kind, host), repository["owner"], repository["name"]
    if kind == "github_pat":
        login = str(_api("GET", f"{base}/user", secret, kind).get("login") or "")
        where = f"{base}/user/repos" if login.lower() == owner.lower() else f"{base}/orgs/{owner}/repos"
        made = _api("POST", where, secret, kind, {"name": name, "private": repository["private"], "auto_init": False,
                                                  "description": "由 KiroCrew ON 创建"}, need=CREATE_NEED)
        return made["html_url"]
    namespace = _api("GET", f"{base}/namespaces/{urllib.parse.quote(owner, safe='')}", secret, kind)["id"]
    made = _api("POST", f"{base}/projects", secret, kind,
                {"name": name, "path": name, "namespace_id": namespace, "initialize_with_readme": False,
                 "visibility": "private" if repository["private"] else "public"}, need=CREATE_NEED)
    return made["web_url"]


def push_baseline(workdir, url, base, auth):
    """Start an empty repository's ``main`` at the Session's baseline (D48 R4); one with any branch is never written."""
    with sources.askpass(Path(workdir), auth) as env:
        listed = _git(workdir, *sources.SAFE_GIT_ARGS, "ls-remote", "--heads", url, env=env, secret=auth["secret"])
        require(not listed.strip(), "仓库已存在且不为空，未发布：改用新的仓库名，或以该仓库为源码新建会话", 409,
                "repository_not_empty")
        try:
            _git(workdir, *sources.SAFE_GIT_ARGS, "push", "-q", url, f"{base}:refs/heads/main", env=env, secret=auth["secret"])
        except Error as exc:
            if "error: 403" not in str(exc):
                raise
            raise Error(409, "credential_permission", "凭证可以读取但不能推送（HTTP 403）：GitHub 细粒度令牌需对该仓库有 "
                        "Contents 读写权限，GitLab 令牌需 write_repository 范围") from None
```

- [ ] **Step 5: 运行，确认通过；旧发布测试不受影响**

Run: `.venv/bin/python -m unittest tests.test_publish_new_repo tests.test_publish tests.test_auto_publish -v`
Expected: 全部 PASS（`test_publish` 中 403 的消息不变）。

- [ ] **Step 6: Commit**

```bash
git add octopus/publish.py tests/test_publish.py tests/test_publish_new_repo.py
git commit -m "feat(publish): 托管方的仓库操作（D48）——查找、建空仓库（GitHub 用户 / 组织、GitLab 命名空间）、空仓库的 main 从会话基线开始，非空仓库拒绝；名称校验，缺建仓库权限时说明需要哪一项"
```

---

### Task 2: 会话发布到新仓库（domain）

**Files:**
- Modify: `octopus/domain.py`（`publish_policy`、`start` 的 publish 校验、`set_publish_policy`、`auto_publish_pending`、`publish`、新增 `_publish_target` 与 `_new_repository_policy`、`_used`、视图投影）
- Modify: `web/src/lib/labels.ts`（`"run.repository": "已新建仓库"`）
- Modify: `tests/test_publish_new_repo.py`

**Interfaces:**
- Consumes: Task 1 的 `check_repository`、`find_repository`、`create_repository`、`push_baseline`。
- Produces:
  - `Service.publish(actor, rid, body)`：body ⊆ `{branch, title, repository, credential}`；返回的记录多 `repository`（网页地址）键。
  - `snapshot["publish_target"] = {"host", "path", "url", "web", "created": bool, "baseline": str | None, "credential": {"id", "name"}, "at", "by"}`；视图字段 `publish_target`。
  - `publish_policy(value)` 接受并返回 `repository`（已校验）与 `credential`（str）。
  - 事件 `run.repository`（payload `url`、`path`、`private`、`created`）。

- [ ] **Step 1: 写失败的测试（追加到 tests/test_publish_new_repo.py）**

```python
import json
import subprocess
from octopus.domain import Service   # noqa: F401  (imported for clarity of what is exercised)


class NewRepositoryCase(PublishCase):
    def setUp(self):
        super().setUp()
        FakeApi.repos = {"owner/app"}
        self.writer = self.service.credentials.create(self.b, {"name": "bob 建仓库", "kind": "github_pat", "host": self.host,
                                                               "scope": "user", "access": "write", "repos": ["owner/*"],
                                                               "secret": SECRET})

    def new_session(self, request="start-0000000801", **body):
        rid = self.service.start(self.b, {"request_id": request, "input": "新建仓库并推 README", "team_id": self.tid,
                                          "worker_id": "w-p", "source": {"type": "new"},
                                          "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}],
                                          "recovery": {"enabled": False, "max_cycles": 2}, **body})["id"]
        result = self.node.prepare({"run_id": rid, "source": {"type": "new"}})
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET workspace=?,native_parent='octopus-x',status='completed' WHERE id=?",
                       (result["workspace"], rid))
        (Path(result["workspace"]) / "README.md").write_text("# fresh\n")
        return rid

    def head(self, path, branch):
        out = subprocess.run(["git", "-C", str(GitHost.root / (path + ".git")), "rev-parse", "--verify", "-q",
                              "refs/heads/" + branch], capture_output=True, text=True)
        return out.stdout.strip() or None

    def target(self, name="fresh", **extra):
        return {"repository": {"owner": "owner", "name": name}, "credential": self.writer["id"], **extra}

    def events(self, rid, kind):
        with self.db.read() as db:
            return [json.loads(r["payload"]) for r in db.execute("SELECT payload FROM events WHERE run_id=? AND kind=?", (rid, kind))]


class NewRepositoryTests(NewRepositoryCase):
    def test_a_new_project_is_published_to_a_repository_the_platform_makes(self):
        rid = self.new_session()
        record = self.service.publish(self.b, rid, self.target())
        base = self.head("owner/fresh", "main")
        self.assertIsNotNone(base)
        self.assertEqual(self.head("owner/fresh", "octopus/" + rid), record["commit"])
        self.assertNotEqual(base, record["commit"])   # main is the baseline; the work is on the Session's branch
        pull = FakeApi.created[-1]
        self.assertEqual((pull["head"], pull["base"], pull["draft"]), ("octopus/" + rid, "main", True))
        self.assertEqual(record["repository"], "https://forge.example/owner/fresh")
        view = self.service.run(self.b, rid)
        self.assertEqual((view["publish_target"]["path"], view["publish_target"]["created"]), ("owner/fresh", True))
        self.assertEqual(len(self.events(rid, "run.repository")), 1)
        with self.db.read() as db:
            uses = [json.loads(r["detail"]) for r in db.execute("SELECT detail FROM audit WHERE action='credential.use'")]
        self.assertTrue(any(u.get("action") == "repo.create" and u["result"] == "created" for u in uses))

    def test_publishing_again_goes_to_the_same_repository(self):
        rid = self.new_session()
        first = self.service.publish(self.b, rid, self.target())
        (Path(self.service.run(self.b, rid)["workspace"]) / "more.txt").write_text("2")
        second = self.service.publish(self.b, rid, {})
        self.assertEqual((second["number"], second["reused"]), (first["number"], True))
        self.assertEqual(len(FakeApi.made), 1)
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, rid, self.target(name="other"))
        self.assertEqual(caught.exception.code, "publish_target_fixed")

    def test_a_publish_that_failed_after_the_repository_was_made_continues_there(self):
        rid = self.new_session()
        FakeApi.refuse = None
        original = __import__("octopus.publish", fromlist=["push"]).push
        calls = []
        def failing(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise Error(409, "publish_failed", "网络中断")
            return original(*args, **kwargs)
        with unittest.mock.patch("octopus.publish.push", failing):
            with self.assertRaises(Error):
                self.service.publish(self.b, rid, self.target())
            self.assertEqual(self.service.run(self.b, rid)["publish_target"]["baseline"] is not None, True)
            record = self.service.publish(self.b, rid, {})
        self.assertEqual(len(FakeApi.made), 1)
        self.assertEqual(self.head("owner/fresh", "octopus/" + rid), record["commit"])

    def test_an_empty_repository_that_already_exists_is_published_to(self):
        FakeApi.repos = {"owner/app", "owner/fresh"}
        bare = GitHost.root / "owner/fresh.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        subprocess.run(["git", "-C", str(bare), "config", "http.receivepack", "true"], check=True)
        rid = self.new_session()
        self.service.publish(self.b, rid, self.target())
        self.assertEqual(FakeApi.made, [])
        self.assertFalse(self.service.run(self.b, rid)["publish_target"]["created"])
        self.assertIsNotNone(self.head("owner/fresh", "main"))

    def test_an_existing_repository_with_content_is_left_alone(self):
        FakeApi.repos = {"owner/app"}
        rid = self.new_session()
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, rid, self.target(name="app"))
        self.assertEqual(caught.exception.code, "repository_not_empty")
        self.assertIsNone(self.head("owner/app", "octopus/" + rid))
        self.assertIsNone(self.service.run(self.b, rid).get("publish_target"))
        self.assertEqual(FakeApi.created, [])

    def test_a_credential_that_does_not_cover_the_new_repository_is_refused_before_any_call(self):
        rid = self.new_session()
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, rid, {"repository": {"owner": "other", "name": "x"}, "credential": self.writer["id"]})
        self.assertEqual(caught.exception.code, "credential_scope")
        self.assertEqual(FakeApi.seen, [])

    def test_a_read_only_credential_or_none_is_refused(self):
        rid = self.new_session()
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, rid, {"repository": {"owner": "owner", "name": "x"}})
        self.assertEqual(caught.exception.code, "publish_target_missing")
        self.service.credentials.update(self.b, self.writer["id"], {"access": "read"})
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, rid, self.target())
        self.assertEqual(caught.exception.code, "credential_read_only")

    def test_a_session_from_a_repository_does_not_take_another_target(self):
        rid, _ = self.session(cred=self.cred["id"])
        with self.assertRaises(Error):
            self.service.publish(self.b, rid, self.target())


class NewRepositoryPolicyTests(NewRepositoryCase):
    def test_a_target_given_at_the_start_is_published_on_completion(self):
        rid = self.new_session(publish={"auto": True, **self.target()})
        self.service.auto_publish_pending()
        view = self.service.run(self.b, rid)
        self.assertTrue(view["publish_auto"]["ok"])
        self.assertEqual(view["publish_target"]["path"], "owner/fresh")

    def test_a_target_for_a_session_from_a_repository_is_refused_at_the_start(self):
        with self.assertRaises(Error):
            self.service.start(self.b, {"request_id": "start-0000000809", "input": "x", "team_id": self.tid, "worker_id": "w-p",
                                        "source": {"type": "git", "url": self.url}, "publish": {"auto": True, **self.target()},
                                        "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}]})

    def test_turning_on_the_automatic_publish_needs_a_repository_for_a_new_source(self):
        rid = self.new_session()
        with self.assertRaises(Error) as caught:
            self.service.set_publish_policy(self.b, rid, {"auto": True})
        self.assertEqual(caught.exception.code, "publish_target_missing")
        self.service.set_publish_policy(self.b, rid, {"auto": True, **self.target()})
        self.service.auto_publish_pending()
        self.assertTrue(self.service.run(self.b, rid)["publish_auto"]["ok"])
```
在文件头部的 import 中加入 `import unittest.mock`。

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m unittest tests.test_publish_new_repo -v`
Expected: Task 1 的测试 PASS；`NewRepositoryTests`、`NewRepositoryPolicyTests` FAIL / ERROR（`只接受 branch 与 title`、`publish 须为 {auto…}`）。

- [ ] **Step 3: 实现 publish_policy 与启动、会话页的核对（octopus/domain.py）**

`publish_policy` 改为：
```python
def publish_policy(value):
    """A Session's publish policy (D47): ``auto`` publishes a Draft PR / MR on its own once the Session completes and passes
    the same checks as the 「发布到仓库」 button; off, a person approves the publish. A new-source Session names the new
    repository and the write credential to publish with (D48)."""
    require(isinstance(value, dict) and set(value) <= {"auto", "branch", "title", "repository", "credential"}
            and isinstance(value.get("auto", False), bool), "publish 须为 {auto: true|false, branch?, title?, repository?, credential?}")
    if value.get("branch") is not None:
        from .publish import check_branch
        check_branch(value["branch"])
    if value.get("title") is not None:
        require(isinstance(value["title"], str) and 0 < len(value["title"].strip()) <= 200, "publish.title 须为 1–200 个字符")
    extra = {}
    if value.get("repository") is not None or value.get("credential") is not None:
        from .publish import check_repository
        require(value.get("repository") is not None and isinstance(value.get("credential"), str) and value["credential"],
                "publish.repository 与 publish.credential 须一起给出", 400)
        extra = {"repository": check_repository(value["repository"]), "credential": value["credential"]}
    return {"auto": bool(value.get("auto", False)), **{k: value[k] for k in ("branch", "title") if value.get(k)}, **extra}
```
在 `Service` 中（`set_publish_policy` 之前）新增：
```python
    @staticmethod
    def _new_repository_policy(db, actor, team_id, source, policy):
        """A policy naming a new repository (D48): only for a new-source Session, with a write credential the actor may
        use for that repository; one turning the automatic publish on for a new source without a target is refused."""
        from . import credentials as repo_credentials
        if policy.get("repository"):
            require(source.get("type") != "git", "会话来自仓库，发布到该仓库：不接受 publish.repository", 400)
            row = db.execute("SELECT * FROM credentials WHERE id=?", (policy["credential"],)).fetchone()
            path = f"{policy['repository']['owner']}/{policy['repository']['name']}"
            repo_credentials.check(db, actor, row, team_id, row["host"] if row else "", path)
            require(row["access"] == "write", f"凭证「{row['name']}」为只读，无法发布", 409, "credential_read_only")
```
`start` 中 `if "publish" in body: publish_policy(body["publish"])` 一处保持；在 `bound = repo_credentials.resolve(...)` 之后加入：
```python
            if "publish" in body:   # D48: a new repository is checked with the credential named for it
                self._new_repository_policy(db, actor, team, source, publish_policy(body["publish"]))
```
`set_publish_policy` 改为：
```python
    def set_publish_policy(self, actor, rid, body):
        """Switch a Session's automatic publish on or off (D47): those who may publish it. A new-source Session publishes
        automatically only to a repository it names or was already published to (D48)."""
        policy = publish_policy(body)
        with self.db.transaction() as db:
            run = run_access(db, actor, rid)
            require(may_publish(db, actor, run), "只有会话创建者、Team owner 或管理员可以设置发布", 403, "forbidden")
            snapshot, source = json.loads(run["snapshot"]), json.loads(run["source"])
            self._new_repository_policy(db, actor, run["team_id"], source, policy)
            require(not policy["auto"] or source.get("type") == "git" or policy.get("repository") or snapshot.get("publish_target"),
                    "新建源码的会话自动发布需要新仓库（repository）与有写权限的凭证（credential）", 409, "publish_target_missing")
            snapshot["publish_policy"] = {**policy, "source": "user"}
            journal.change(db, "runs", rid, journal.person(actor), "run.publish_policy", quiet=("snapshot",), snapshot=dumps(snapshot))
            journal.record(db, rid, "run.publish_policy", journal.person(actor), "完成后自动发布：" + ("开" if policy["auto"] else "关"),
                           auto=policy["auto"])
        return {"id": rid, "publish_policy": snapshot["publish_policy"]}
```
`auto_publish_pending` 中调用改为：
```python
                record = self.publish(actor, row["id"], {k: policy[k] for k in ("branch", "title", "repository", "credential")
                                                         if policy.get(k)})
```

- [ ] **Step 4: 实现发布目标与新仓库的发布（octopus/domain.py）**

在 `publish` 之前新增：
```python
    def _publish_target(self, db, actor, run, snapshot, source, body):
        """Where a publish goes (D48): a git Session's own repository with its bound credential; a new-source Session's
        repository it was first published to, or the new one ``body`` names with the credential named for it."""
        from . import credentials as repo_credentials, publish as publishing
        if source.get("type") == "git":
            require(not {"repository", "credential"} & set(body), "会话来自仓库，发布到该仓库：不接受 repository 与 credential", 400)
            bound = (snapshot.get("credentials") or {}).get("source")
            require(bound, "会话没有绑定凭证：先在会话页绑定一枚有写权限的凭证", 409, "credential_unavailable")
            row = db.execute("SELECT * FROM credentials WHERE id=?", (bound["id"],)).fetchone()
            require(repo_credentials.bound_usable(db, row, run),
                    f"凭证「{bound['name']}」已不能用于此会话（已停用、删除、授权撤销或所有者已离开）", 409, "credential_unavailable")
            try:
                host, path = repo_credentials.repo_path(source.get("url", ""))
            except ValueError as exc:
                raise Error(409, "credential_https_only", str(exc)) from None
            require(row["host"] == host and repo_credentials.covers(json.loads(row["repos"]), path) is not None,
                    f"凭证「{row['name']}」不覆盖仓库 {path}", 409, "credential_unavailable")
            return {"row": row, "host": host, "path": path, "url": source["url"], "target": source.get("ref"), "new": None}
        recorded = snapshot.get("publish_target")
        if recorded:
            if body.get("repository"):
                wanted = publishing.check_repository(body["repository"])
                require(f"{wanted['owner']}/{wanted['name']}" == recorded["path"],
                        f"此会话已发布到 {recorded['path']}，不能改到另一个仓库", 409, "publish_target_fixed")
            row = db.execute("SELECT * FROM credentials WHERE id=?", (body.get("credential") or recorded["credential"]["id"],)).fetchone()
            repo_credentials.check(db, actor, row, run["team_id"], recorded["host"], recorded["path"])
            return {"row": row, "host": recorded["host"], "path": recorded["path"], "url": recorded["url"], "target": "main",
                    "new": None if recorded.get("baseline") else {"recorded": recorded}}
        require(body.get("repository") and body.get("credential"),
                "新建源码的会话发布时需要新仓库（repository: {owner, name}）与一枚有写权限的凭证（credential）", 409,
                "publish_target_missing")
        wanted = publishing.check_repository(body["repository"])
        row = db.execute("SELECT * FROM credentials WHERE id=?", (body["credential"],)).fetchone()
        path = f"{wanted['owner']}/{wanted['name']}"
        repo_credentials.check(db, actor, row, run["team_id"], row["host"] if row else "", path)
        return {"row": row, "host": row["host"], "path": path, "url": f"https://{row['host']}/{path}.git", "target": "main",
                "new": {"repository": wanted}}

    def _record_target(self, actor, rid, target, row, **fields):
        """Note the new-source Session's repository (D48 R6) so a retry or the next publish goes there again."""
        with self.db.transaction() as db:
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
            current = snapshot.get("publish_target") or {
                "host": target["host"], "path": target["path"], "url": target["url"], "web": None, "created": False,
                "baseline": None, "credential": {"id": row["id"], "name": row["name"]}, "at": time.time(),
                "by": {"id": actor["id"], "username": actor.get("username")}}
            snapshot["publish_target"] = {**current, **fields}
            journal.change(db, "runs", rid, journal.person(actor), "run.repository", quiet=("snapshot",), snapshot=dumps(snapshot))
            return snapshot["publish_target"]
```
`publish` 改为（只列出与现状不同的部分；其余照旧）：
```python
        require(isinstance(body, dict) and set(body) <= {"branch", "title", "repository", "credential"},
                "只接受 branch、title、repository 与 credential")
        with self.db.read() as db:
            run = run_access(db, actor, rid)
            require(may_publish(db, actor, run), "只有会话创建者、Team owner 或管理员可以发布", 403, "forbidden")
            require(run["lifecycle"] == "open" and run["status"] in SETTLED and run["workspace"],
                    "会话执行结束后才能发布", 409, "publish_busy")
            snapshot, source = json.loads(run["snapshot"]), json.loads(run["source"])
            target = self._publish_target(db, actor, run, snapshot, source, body)
            row, host, path = target["row"], target["host"], target["path"]
            require(row["access"] == "write", f"凭证「{row['name']}」为只读，无法发布", 409, "credential_read_only")
            refused = repo_credentials.refused_address(row["host"], self.credentials.private_hosts)
            require(not refused, refused, 409, "credential_host_refused")
            verified = ...   # unchanged
            config = ...     # unchanged
        branch = publishing.check_branch(body.get("branch") or "octopus/" + rid, source.get("ref"))
        title = ...      # unchanged
        secret = ...     # unchanged
        auth = ...       # unchanged
        new = target["new"]
        if new and "repository" in new:   # D48: the first publish of a new-source Session
            found = publishing.find_repository(row["kind"], host, path, secret)
            if found is None:
                try:
                    web = publishing.create_repository(row["kind"], host, new["repository"], secret)
                except Error as exc:
                    with self.db.transaction() as db:
                        self._used(db, actor, row["id"], rid, path, None, exc.code, action="repo.create")
                    raise
                recorded = self._record_target(actor, rid, target, row, web=web, created=True)
                with self.db.transaction() as db:
                    self._used(db, actor, row["id"], rid, path, None, "created", action="repo.create")
                    journal.record(db, rid, "run.repository", journal.person(actor), "已新建仓库 " + path, url=web, path=path,
                                   private=new["repository"]["private"], created=True)
                new = {"recorded": recorded}
        response = worker_stream(...)   # unchanged
        with response, tempfile.TemporaryDirectory(dir=self.db.root, prefix=".publish-") as work:
            headers = response.headers
            commit = publishing.receive(...)   # unchanged
            try:
                if new:   # main starts at the baseline, in a repository that is empty (made now, or found empty)
                    publishing.push_baseline(Path(work) / "repo", target["url"], headers.get("X-Publish-Base") or "", auth)
                    if "recorded" not in new:
                        with self.db.transaction() as db:
                            journal.record(db, rid, "run.repository", journal.person(actor), "发布到已有的空仓库 " + path,
                                           path=path, created=False)
                    self._record_target(actor, rid, target, row, baseline=headers.get("X-Publish-Base"))
                pushed = publishing.push(Path(work) / "repo", target["url"], branch, commit, auth)
            except Error as exc:   # the credential was used all the same
                with self.db.transaction() as db:
                    self._used(db, actor, row["id"], rid, path, branch, exc.code)
                raise
```
其后：`pull_request(row["kind"], host, path, branch, target["target"], title, …)`；`record` 增加
`**({"repository": (snapshot_target or {}).get("web") or f"https://{host}/{path}"} if source.get("type") != "git" else {})`，其中
`snapshot_target` 为写入前重新读取的 `snapshot.get("publish_target")`（在写 `snapshot["publish"]` 的事务里取同一份 snapshot）。

`_used` 改为：
```python
    @staticmethod
    def _used(db, actor, credential, rid, repo, branch, result, action=None):
        """Every use of a write credential is audited, whatever came of it (D35 §6); creating a repository too (D48)."""
        db.execute("UPDATE credentials SET last_used=? WHERE id=?", (time.time(), credential))
        audit(db, actor, "credential.use", "credential", credential,
              {"run_id": rid, "repo": repo, "access": "write", "branch": branch, "result": result,
               **({"action": action} if action else {})})
```
视图投影（`value.update({key: snapshot.get(key) for key in (...)`）的元组末尾加入 `"publish_target", "publish_suggestion"`。

`web/src/lib/labels.ts` 的 `EVENT_LABELS` 加入 `"run.repository": "已新建仓库",`。

- [ ] **Step 5: 运行，确认通过**

Run: `.venv/bin/python -m unittest tests.test_publish_new_repo tests.test_publish tests.test_auto_publish tests.test_event_labels -v`
Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add octopus/domain.py web/src/lib/labels.ts tests/test_publish_new_repo.py
git commit -m "feat(publish): 新建源码的会话发布到新仓库（D48）——发布时给出 repository 与写凭证，平台建仓库（或用已有的空仓库）、基线到 main、工作走 Draft PR；只建一次并记 publish_target，再次发布与自动发布沿用；非空仓库与范围不覆盖时拒绝；建仓库写审计与时间线"
```

---

### Task 3: 规划者的建议与「需要处理」（R7）

**Files:**
- Modify: `octopus/workflow_import.py:143-144`
- Modify: `octopus/planning.py`（规则文字、`expand` 的说明与 `publish_suggestion`）
- Modify: `octopus/domain.py`（`_attention`：新建源码的推送失败、完成后待确认）
- Modify: `tests/test_auto_publish.py`
- Modify: `tests/test_publish_new_repo.py`

**Interfaces:**
- Consumes: Task 1 `check_repository`；Task 2 `publish_target`。
- Produces: `snapshot["publish_suggestion"] = {"repository": {...}, "quote": str}`；视图同名字段（Task 2 已投影）。

- [ ] **Step 1: 写失败的测试**

`tests/test_auto_publish.py`：把 `test_a_session_without_a_repository_is_not_sent_to_publish` 的断言改为
```python
        self.assertEqual(run["attention"]["title"], "Agent 不能推送或创建 PR")
        self.assertNotIn("「发布到仓库」", run["attention"]["action"])
        self.assertIn("新建仓库并发布", run["attention"]["action"])
```
把 `test_a_session_without_a_repository_says_so_instead_of_publishing` 的说明断言改为
```python
        self.assertTrue(any("要求推送或创建 PR" in note and "没有远端仓库" in note for note in notes))
        self.assertTrue(any("未开启自动发布" in note for note in notes))
```
（保持不变；Step 3 的新文字仍含这些短语。）并在 `PlannedTests` 中追加：
```python
    def test_a_requirement_to_create_a_repository_is_suggested_not_created(self):
        import tests.test_confirmation as confirmation
        plan = PLAN.replace('"publish": {"quote": "完成后提交 PR"}',
                            '"publish": {"quote": "新建 LiboMa/demo 仓库", "repository": {"owner": "LiboMa", "name": "demo"}}')
        original, confirmation.PLAN = confirmation.PLAN, plan
        self.addCleanup(setattr, confirmation, "PLAN", original)
        run = self.tick(self.start(steps=None, plan="agent"))
        self.assertEqual(run["publish_suggestion"]["repository"], {"owner": "LiboMa", "name": "demo", "private": True})
        self.assertFalse((run.get("publish_policy") or {}).get("auto"))
        self.assertTrue(any("LiboMa/demo" in note and "不自动创建" in note for note in run["planning"]["notes"]))

    def test_an_invalid_suggested_repository_is_dropped_with_a_note(self):
        import tests.test_confirmation as confirmation
        plan = PLAN.replace('"publish": {"quote": "完成后提交 PR"}',
                            '"publish": {"quote": "建仓库", "repository": {"owner": "a/b", "name": "../x"}}')
        original, confirmation.PLAN = confirmation.PLAN, plan
        self.addCleanup(setattr, confirmation, "PLAN", original)
        run = self.tick(self.start(steps=None, plan="agent"))
        self.assertIsNone(run.get("publish_suggestion"))
        self.assertTrue(any("仓库名不合法" in note for note in run["planning"]["notes"]))
```
`tests/test_publish_new_repo.py` 追加：
```python
class SuggestionAttentionTests(NewRepositoryCase):
    def test_a_completed_session_with_a_suggested_repository_asks_for_confirmation(self):
        rid = self.new_session()
        with self.db.transaction() as db:
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
            snapshot["publish_suggestion"] = {"repository": {"owner": "owner", "name": "fresh", "private": True}, "quote": "建仓库"}
            db.execute("UPDATE runs SET snapshot=? WHERE id=?", (json.dumps(snapshot), rid))
        attention = self.service.run(self.b, rid)["attention"]
        self.assertEqual(attention["title"], "发布到新仓库待确认")
        self.assertIn("owner/fresh", attention["detail"])
        self.service.publish(self.b, rid, self.target())
        self.assertIsNone(self.service.run(self.b, rid)["attention"])
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m unittest tests.test_auto_publish tests.test_publish_new_repo -v`
Expected: 新增与修改的 4 个测试 FAIL（`KeyError: 'publish_suggestion'`、文字不符、attention 为 None）。

- [ ] **Step 3: 实现**

`octopus/workflow_import.py` 第 143–144 行改为：
```python
        if isinstance(document.get("publish"), dict):   # D47: the requirement asked for a pull / merge request
            result["publish"] = {"quote": str(document["publish"].get("quote") or "")[:200]}
            if document["publish"].get("repository") is not None:   # D48: a new repository it named — a suggestion only
                result["publish"]["repository"] = document["publish"]["repository"]
```
`octopus/planning.py` 规则文字（第 51 行）改为：
```
   需求明确要求提交 PR / MR 时，在计划 JSON 顶层写 "publish": {{"quote": "需求原文片段"}}：会话完成并通过审查后平台自动发布 Draft PR。
   需求要求新建仓库时再写 "repository": {{"owner": "账户或组织", "name": "仓库名"}}：平台只记为建议，由用户确认后发布，不安排建仓库的任务。
```
`expand` 中 D47 的步骤说明改为：
```python
            notes.append(f"任务「{step['name']}」要求推送或创建 PR：Agent 没有仓库写凭证，" + (
                "发布由平台在会话完成后进行" if repository
                else "此会话的源码不来自远端仓库：没有远端仓库，会话完成后可在会话页发布到新仓库"))
```
publish 分支改为：
```python
    if plan.get("publish") and (snapshot.get("publish_policy") or {}).get("source") != "user":
        # D47: the requirement asked for a pull / merge request — published on completion; the user's own setting wins
        if repository:
            snapshot["publish_policy"] = {"auto": True, "source": "requested", "quote": plan["publish"].get("quote", "")}
        else:
            suggested = plan["publish"].get("repository")
            if suggested is not None:
                from .publish import check_repository
                try:   # D48 R7: what the requirement named, for the person to confirm; never created on its own
                    snapshot["publish_suggestion"] = {"repository": check_repository(suggested),
                                                      "quote": plan["publish"].get("quote", "")}
                    name = f"{suggested['owner']}/{suggested['name']}"
                    notes.append(f"需求要求新建仓库 {name}：平台不自动创建，会话完成后在会话页确认仓库名与凭证后发布")
                except Error:
                    notes.append("需求要求新建仓库，但规划给出的仓库名不合法，未记为建议：会话完成后在会话页填写")
            notes.append("需求要求提交 PR，但此会话的源码不来自远端仓库，没有远端仓库，未开启自动发布")
```
（`Error` 已在 planning.py 导入；若没有，加 `from .security import Error`。）

`octopus/domain.py` `_attention`：在 `if run["status"] == "completed" and (run.get("publish_auto") or {}).get("ok") is False:` 之前加入：
```python
        suggested = (run.get("publish_suggestion") or {}).get("repository")
        if run["status"] == "completed" and suggested and not run.get("publish_target"):
            name = f"{suggested['owner']}/{suggested['name']}"
            return {"title": "发布到新仓库待确认", "detail": f"需求要求新建仓库 {name}，平台不会自动创建。",
                    "action": "在会话页核对 owner、仓库名与凭证后点「新建仓库并发布」；Coding Agent 经用户同意后用 octopus_publish "
                              "（repository、credential）。", "task_id": None}
```
推送失败（非 git 源码）的 action 改为：
```python
                action = ("此会话的源码不来自远端仓库：推送与建仓库由平台完成——会话完成后在会话页填写新仓库（owner、仓库名）与有写权限的凭证，"
                          "点「新建仓库并发布」；可续作去掉要求建仓库或推送的任务。")
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m unittest tests.test_auto_publish tests.test_publish_new_repo tests.test_confirmation -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add octopus/workflow_import.py octopus/planning.py octopus/domain.py tests/test_auto_publish.py tests/test_publish_new_repo.py
git commit -m "feat(planning): 需求要求新建仓库时规划者只记建议 publish_suggestion（不合法的仓库名丢弃并说明），不开启自动发布、不创建；完成后「需要处理」为「发布到新仓库待确认」；新建源码的推送失败提示改为在会话页新建仓库并发布（D48 R7）"
```

---

### Task 4: API、MCP、Skill 与运维文档

**Files:**
- Modify: `octopus/schema.py:73,82`（`PublishPolicy`、`Start.publish`，`Publish` 请求体）
- Modify: `octopus/mcp.py`（`octopus_publish`、`octopus_start.publish`、`octopus_publish_policy`、精简状态）
- Modify: `skills/kirocrew-on/SKILL.md:56-58,125`
- Modify: `docs/OPERATIONS.md`（D47 节「发布到会话启动时的仓库…平台目前不能新建仓库」一句）
- Test: `tests/test_auto_publish.py`（`McpTests`）

**Interfaces:**
- Consumes: Task 2 的 body 与字段名。

- [ ] **Step 1: 写失败的测试（追加到 McpTests）**

```python
    def test_a_new_repository_can_be_named_through_the_tools(self):
        from octopus import mcp, schema
        tools = {t["name"]: t["inputSchema"]["properties"] for t in mcp.TOOLS}
        self.assertTrue({"repository", "credential"} <= set(tools["octopus_publish"]))
        self.assertTrue({"repository", "credential"} <= set(tools["octopus_start"]["publish"]["properties"]))
        self.assertTrue({"repository", "credential"} <= set(tools["octopus_publish_policy"]))
        components = schema.openapi()["components"]["schemas"]
        self.assertIn("repository", components["PublishPolicy"]["properties"])
        self.assertIn("repository", components["Start"]["properties"]["publish"]["properties"])
        self.assertIn("repository", components["Publish"]["properties"])

    def test_the_compact_status_shows_where_it_was_published(self):
        from octopus import mcp
        view = mcp.compact_status({"id": "run-1", "status": "completed", "tasks": [],
                                   "publish_target": {"path": "o/r", "web": "https://x/o/r"},
                                   "publish_suggestion": {"repository": {"owner": "o", "name": "r", "private": True}}}, [])["session"]
        self.assertEqual(view["publish_target"]["path"], "o/r")
        self.assertIn("publish_suggestion", view)
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m unittest tests.test_auto_publish -k McpTests -v`
Expected: FAIL（缺 `repository` 属性、`Publish` 组件不存在）。

- [ ] **Step 3: 实现**

`octopus/schema.py`：定义一次
```python
        repository = obj({"owner": text, "name": text, "private": {"type": "boolean", "default": True}}, ["owner", "name"])
```
（放在 `PublishPolicy` 之前，与 `text` 同一作用域），并把
```python
        "PublishPolicy": obj({"auto": {"type": "boolean"}, "branch": text, "title": text, "repository": repository, "credential": text}, ["auto"]),
```
`Start` 中 `"publish": obj({"auto": {...}, "branch": text, "title": text, "repository": repository, "credential": text})`；新增组件
```python
        "Publish": obj({"branch": text, "title": text, "repository": repository, "credential": text}, []),
```
并在路由表 `"/v1/runs/{id}/publish-policy": {"post": "PublishPolicy"},` 之前加入 `"/v1/runs/{id}/publish": {"post": "Publish"},`（目前 OpenAPI 未列出该路由）。

`octopus/mcp.py`：
- `octopus_publish` 的 description 末尾加：「新建源码的会话首次发布需给出 repository（{owner, name, private?}，默认私有）与 credential（有写权限且仓库范围覆盖该仓库的凭证 ID）：平台新建仓库（或用已有的空仓库），基线推到 main，工作走 Draft PR；之后再次发布沿用该仓库。只在用户明确给出仓库名并同意时传。」；inputSchema 增加 `"repository": REPOSITORY, "credential": STRING`。
- 模块级定义 `REPOSITORY = object_schema({"owner": STRING, "name": STRING, "private": {"type": "boolean"}}, ["owner", "name"])`。
- `octopus_start` 的 `publish` 改为 `object_schema({"auto": {"type": "boolean"}, "branch": STRING, "title": STRING, "repository": REPOSITORY, "credential": STRING})`。
- `octopus_publish_policy` inputSchema 增加 `"repository": REPOSITORY, "credential": STRING`；调用处把这两个键原样放进请求体（`{k: args[k] for k in ("auto", "repository", "credential") if k in args}`）。
- 精简状态的循环改为 `for key in ("publish_policy", "publish_auto", "publish_target", "publish_suggestion"):   # D47, D48`。

`skills/kirocrew-on/SKILL.md` 第 56–58 行之后加：
```
- 需求要求**新建仓库**（会话的源码是新建的）时：用户明确给出 owner 与仓库名时，`octopus_start` 传 `publish: {auto: true, repository: {owner, name}, credential: <凭证 ID>}`
  （凭证须有写权限、范围覆盖该仓库、能建仓库），完成后平台自动建仓库并发布；用户没给出时不传，完成后先问用户仓库名与凭证，同意后
  `octopus_publish {run_id, repository, credential}`。不要安排 Agent 自己建仓库或推送。
```
`docs/OPERATIONS.md` D47 节中「平台目前不能新建仓库（EC2 `run-f7670bfb…`）。」改为：
```
新建源码的会话可发布到新仓库（D48）：发布时给出 `repository: {owner, name, private?}`（默认私有）与有写权限、范围覆盖该仓库、能建仓库的凭证，
平台建仓库（或用已有的空仓库）、基线推到 `main`、工作走 Draft PR，只建一次（`publish_target`）；非空的同名仓库拒绝。规划者识别到建仓库只记建议，
完成后「需要处理」为「发布到新仓库待确认」，不自动创建。
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m unittest tests.test_auto_publish tests.test_mvp -v`
Expected: 全部 PASS（`test_openapi_inputs_cover_direct_custom_tasks` 仍通过）。

- [ ] **Step 5: Commit**

```bash
git add octopus/schema.py octopus/mcp.py skills/kirocrew-on/SKILL.md docs/OPERATIONS.md tests/test_auto_publish.py
git commit -m "feat(mcp): D48 的 API / MCP / Skill——octopus_publish、octopus_start.publish、octopus_publish_policy 接受 repository 与 credential，精简状态含 publish_target 与 publish_suggestion；OpenAPI 增加 Publish 请求体；OPERATIONS 与 Skill 写明新建仓库的发布方式"
```

---

### Task 5: 控制台（会话页与向导）

**Files:**
- Modify: `web/src/api/types.ts`（`Run.publish_target`、`Run.publish_suggestion`、`publish_policy.repository/credential`、`PublishRecord.repository`）
- Modify: `web/src/features/sessions/PublishControl.tsx`
- Create: `web/src/features/sessions/PublishControl.test.tsx`
- Modify: `web/src/features/launch/state.ts`、`web/src/features/launch/steps.tsx`、`web/src/features/launch/state.test.ts`

**Interfaces:**
- Consumes: Task 2 的视图字段与 `POST /runs/<id>/publish` body。
- Produces: `NewRepositoryPublish`（PublishControl.tsx 内部组件）；Draft 字段 `newRepoOwner`、`newRepoName`、`newRepoCredential`。

- [ ] **Step 1: 写失败的测试**

`web/src/features/sessions/PublishControl.test.tsx`：
```tsx
import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setToken } from "@/api/client";
import type { Run } from "@/api/types";
import { makeRun, mockApi } from "@/test/api";
import { PublishControl } from "@/features/sessions/PublishControl";

afterEach(() => vi.unstubAllGlobals());

const writer = { id: "cred-w", name: "我的 GitHub", kind: "github_pat", host: "github.com", scope: "user", owner: { id: "u1" },
  repos: ["LiboMa/*"], access: "write", hint: "…f20h", expires: null, created: 1, updated: 1, last_used: null, disabled: false,
  can_manage: true };

function show(run: Run, routes: Record<string, unknown> = {}) {
  setToken("oct_fixture");
  const calls = mockApi({ "GET /credentials": [writer], [`GET /runs/${run.id}`]: run, ...routes });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><PublishControl run={run} /></QueryClientProvider>);
  return calls;
}

describe("publishing a new project to a new repository (D48)", () => {
  test("the suggested repository is filled in and published with the chosen credential", async () => {
    const run = makeRun({ status: "completed", source: { type: "new" },
      publish_suggestion: { repository: { owner: "LiboMa", name: "demo", private: true }, quote: "新建仓库" } });
    const calls = show(run, { [`POST /runs/${run.id}/publish`]: { branch: "octopus/run-a", commit: "c", url: "https://github.com/LiboMa/demo/pull/1",
      number: 1, kind: "pull_request", reused: false, at: 1, by: { id: "u1" }, repository: "https://github.com/LiboMa/demo" } });
    expect(await screen.findByLabelText("owner")).toHaveValue("LiboMa");
    expect(screen.getByLabelText("仓库名")).toHaveValue("demo");
    await userEvent.selectOptions(await screen.findByLabelText("凭证"), "cred-w");
    await userEvent.click(screen.getByRole("button", { name: "新建仓库并发布" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/publish"))).toBe(true));
    const sent = calls.find((c) => c.method === "POST" && c.path.endsWith("/publish"))!.body;
    expect(sent).toEqual({ repository: { owner: "LiboMa", name: "demo", private: true }, credential: "cred-w" });
  });

  test("once published, the repository is shown and publishing again sends no target", async () => {
    const run = makeRun({ status: "completed", source: { type: "new" },
      publish_target: { host: "github.com", path: "LiboMa/demo", url: "https://github.com/LiboMa/demo.git", web: "https://github.com/LiboMa/demo",
        created: true, baseline: "b", credential: { id: "cred-w", name: "我的 GitHub" }, at: 1, by: { id: "u1" } } });
    const calls = show(run, { [`POST /runs/${run.id}/publish`]: { branch: "octopus/run-a", commit: "c", url: null, number: null,
      kind: null, reused: true, at: 1, by: { id: "u1" } } });
    expect(await screen.findByRole("link", { name: "LiboMa/demo" })).toHaveAttribute("href", "https://github.com/LiboMa/demo");
    await userEvent.click(screen.getByRole("button", { name: "再次发布到仓库" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path.endsWith("/publish"))?.body).toEqual({}));
  });
});
```
`web/src/features/launch/state.test.ts` 追加（放在 D47 的 `autoPublish` 用例旁）：
```ts
  test("a new project can be published to a new repository on completion (D48)", () => {
    const body = submitBody(draft({ method: "agent", tasks: [], source: "new", autoPublish: true,
      newRepoOwner: "LiboMa", newRepoName: "demo", newRepoCredential: "cred-w" }));
    expect(body.publish).toEqual({ auto: true, repository: { owner: "LiboMa", name: "demo", private: true }, credential: "cred-w" });
    expect(submitBody(draft({ method: "agent", tasks: [], source: "new", autoPublish: true })).publish).toBeUndefined();
  });
```

- [ ] **Step 2: 运行，确认失败**

Run: `npx --prefix web vitest run --root web src/features/sessions/PublishControl.test.tsx src/features/launch/state.test.ts`
Expected: FAIL（PublishControl 对新建源码返回 null；`newRepoOwner` 不是 Draft 字段）。

- [ ] **Step 3: 实现**

`types.ts`：
```ts
  /** D48: the repository a new-source Session was first published to; later publishes go there. */
  publish_target?: { host: string; path: string; url: string; web?: string | null; created: boolean; baseline?: string | null;
    credential: { id: string; name: string }; at: number; by: { id: string; username?: string | null } } | null;
  /** D48 R7: a new repository the requirement named, for the person to confirm (never created on its own). */
  publish_suggestion?: { repository: { owner: string; name: string; private: boolean }; quote?: string } | null;
```
`publish_policy` 类型加 `repository?: { owner: string; name: string; private: boolean }; credential?: string`；`PublishRecord` 加 `repository?: string | null;`。

`PublishControl.tsx`：在 `if (run.source.type !== "git" || !source) return null;` 之前加入
```tsx
  if (run.source.type === "new") return <NewRepositoryPublish run={run} />;
```
并在文件末尾新增：
```tsx
/** D48: a new project's work published to a repository the platform creates (or an empty one): owner, name, private,
 *  and a write credential; once published, the repository is kept and later publishes go there. */
function NewRepositoryPublish({ run }: { run: Run }) {
  const refresh = useRefreshRun();
  const target = run.publish_target;
  const suggested = run.publish_suggestion?.repository;
  const [owner, setOwner] = useState(suggested?.owner ?? "");
  const [name, setName] = useState(suggested?.name ?? "");
  const [isPrivate, setPrivate] = useState(suggested?.private ?? true);
  const [credential, setCredential] = useState("");
  const [busy, setBusy] = useState(false);
  const { data = [] } = useCredentials(!target);
  const writers = data.filter((c) => c.access === "write" && !c.disabled);
  const published = run.publish;
  const settled = run.lifecycle === "open" && SETTLED.has(run.status) && run.permissions?.publish !== false;
  const send = async (body: Record<string, unknown>) => {
    setBusy(true);
    try {
      const record = await api<PublishRecord>("/runs/" + encodeURIComponent(run.id) + "/publish", "POST", body);
      await refresh(run.id);
      toast.success(`已发布到 ${record.branch}${record.number ? `：PR #${record.number}` : ""}`);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (target) {
    return (
      <p id="run-publish" className="text-xs text-muted-foreground">
        仓库：<a className="underline" href={target.web ?? target.url} target="_blank" rel="noreferrer">{target.path}</a>
        {published?.url && <> · <a className="underline" href={published.url} target="_blank" rel="noreferrer">PR #{published.number}</a></>}{" "}
        {settled && <Button type="button" size="xs" variant="outline" disabled={busy} onClick={() => send({})}>
          {busy ? "正在发布…" : "再次发布到仓库"}</Button>}
      </p>
    );
  }
  if (!settled) return null;
  return (
    <div id="run-publish" className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span>发布到新仓库：</span>
      <label className="inline-flex items-center gap-1">owner
        <input aria-label="owner" className="h-6 w-28 rounded border px-1" value={owner} onChange={(e) => setOwner(e.target.value)} /></label>
      <label className="inline-flex items-center gap-1">仓库名
        <input aria-label="仓库名" className="h-6 w-32 rounded border px-1" value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="inline-flex items-center gap-1">
        <input type="checkbox" checked={isPrivate} onChange={(e) => setPrivate(e.target.checked)} />私有</label>
      <label className="inline-flex items-center gap-1">凭证
        <select aria-label="凭证" className="h-6 rounded border px-1" value={credential} onChange={(e) => setCredential(e.target.value)}>
          <option value="">选择有写权限的凭证</option>
          {writers.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.host} · {c.hint}</option>)}
        </select></label>
      <Button type="button" size="xs" variant="outline" disabled={busy || !owner.trim() || !name.trim() || !credential}
        onClick={() => send({ repository: { owner: owner.trim(), name: name.trim(), private: isPrivate }, credential })}>
        {busy ? "正在发布…" : "新建仓库并发布"}
      </Button>
      {run.publish_suggestion && <span>（需求中要求：{run.publish_suggestion.quote}）</span>}
    </div>
  );
}
```
import 增加 `import { useCredentials } from "@/api/hooks";`。

`state.ts`：`Draft` 增加
```ts
  /** D48: a new project's new repository to publish to on completion (with autoPublish). */
  newRepoOwner: string;
  newRepoName: string;
  newRepoCredential: string;
```
初始值 `newRepoOwner: "", newRepoName: "", newRepoCredential: ""`；`submitBody` 中 D47 那一行改为：
```ts
    ...(draft.source === "git" && draft.autoPublish ? { publish: { auto: true } } : {}),
    ...(draft.source === "new" && draft.autoPublish && draft.newRepoOwner.trim() && draft.newRepoName.trim() && draft.newRepoCredential
      ? { publish: { auto: true, repository: { owner: draft.newRepoOwner.trim(), name: draft.newRepoName.trim(), private: true },
                     credential: draft.newRepoCredential } } : {}),
```
`steps.tsx`：在新建源码分支（`draft.source === "new"` 的来源说明处；用 `grep -n "source === \"new\"" web/src/features/launch/steps.tsx` 定位）加入一个 `NewRepositoryChoice` 组件：
```tsx
/** D48: publish a new project to a new repository once it completes (private; the credential must be able to create it). */
function NewRepositoryChoice({ draft, update }: { draft: Draft; update: Update }) {
  const { data = [] } = useCredentials(true);
  const writers = data.filter((c) => c.access === "write" && !c.disabled);
  return (
    <div className="space-y-1.5 sm:col-span-2 text-xs">
      <label className="flex items-center gap-2">
        <input type="checkbox" id="new-repo-publish" checked={draft.autoPublish}
          onChange={(e) => update((d) => ({ ...d, autoPublish: e.target.checked }))} />
        完成后发布到新仓库（平台新建私有仓库，基线进 main，工作走 Draft PR）
      </label>
      {draft.autoPublish && (
        <div className="flex flex-wrap gap-2">
          <Input aria-label="新仓库 owner" placeholder="owner" value={draft.newRepoOwner}
            onChange={(e) => update((d) => ({ ...d, newRepoOwner: e.target.value }))} />
          <Input aria-label="新仓库名" placeholder="仓库名" value={draft.newRepoName}
            onChange={(e) => update((d) => ({ ...d, newRepoName: e.target.value }))} />
          <NativeSelect aria-label="新仓库凭证" value={draft.newRepoCredential}
            onChange={(e) => update((d) => ({ ...d, newRepoCredential: e.target.value }))}>
            <option value="">选择有写权限的凭证</option>
            {writers.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.host} · {c.hint}</option>)}
          </NativeSelect>
        </div>
      )}
    </div>
  );
}
```
并在新建源码时渲染 `<NewRepositoryChoice draft={draft} update={update} />`。

- [ ] **Step 4: 运行，确认通过**

Run: `npx --prefix web vitest run --root web` 与 `npm --prefix web run build`
Expected: 全部 PASS，构建成功。

- [ ] **Step 5: Commit**

```bash
git add web/src/api/types.ts web/src/features/sessions/PublishControl.tsx web/src/features/sessions/PublishControl.test.tsx web/src/features/launch/state.ts web/src/features/launch/steps.tsx web/src/features/launch/state.test.ts
git commit -m "feat(console): 新建源码的会话页「发布到新仓库」（owner、仓库名、私有、写凭证；带入规划者的建议），发布后显示仓库并可再次发布；向导「完成后发布到新仓库」（D48）"
```

---

### Task 6: 全量回归、终审与 EC2 验收

- [ ] **Step 1: 全量回归**

Run: `PYTHON=/Users/malibo/MyDev/Kiro-Family-Spaces/experiments/kirocrew-on/.venv/bin/python bash tools/regress.sh`
Expected: `regress: OK`。

- [ ] **Step 2: 终审（opus 子代理，按 executing-plans 的 Final Review）并修复 Critical / Important。**

- [ ] **Step 3: EC2 验收（需负责人同意账户与仓库名后进行）**

部署后用 `/tmp/kc_accept_d48.py`：新建源码的会话（实现 README + 只读审查），`publish: {auto: true, repository: {owner: <负责人同意的账户>, name: <负责人同意的名称>, private: true}, credential: <能建仓库的写凭证>}`；
核对会话完成、仓库被创建为私有、`main` 为基线、`octopus/<run>` 与 Draft PR 存在、会话页显示仓库链接、时间线「已新建仓库」。验收后由负责人决定是否删除该仓库。
