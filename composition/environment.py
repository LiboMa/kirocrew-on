"""Process environment for the composed Gateway, derived only from Settings."""
import os
import shutil
from urllib.parse import urlsplit


def trust_browser_entry(settings, environ):
    """Let KiroCrew's Host and CSRF checks accept the origin the console opens the native chat at (crew_url).

    A public origin (CloudFront) joins KIROCREW_CORS_ORIGINS; a loopback origin on another port (the local end of an
    SSM tunnel) joins KIROCREW_ALLOWED_LOOPBACK_PORTS. The Gateway itself keeps binding loopback only.
    """
    if not settings.crew_url:
        return
    parts = urlsplit(settings.crew_url)
    if parts.hostname in {"localhost", "127.0.0.1", "::1"}:
        if parts.port and parts.port != settings.gateway_port:
            ports = [p for p in environ.get("KIROCREW_ALLOWED_LOOPBACK_PORTS", "").split(",") if p]
            environ["KIROCREW_ALLOWED_LOOPBACK_PORTS"] = ",".join(dict.fromkeys([*ports, str(parts.port)]))
        return
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    origins = [o for o in environ.get("KIROCREW_CORS_ORIGINS", "").split(",") if o]
    environ["KIROCREW_CORS_ORIGINS"] = ",".join(dict.fromkeys([*origins, origin]))


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
    trust_browser_entry(settings, environ)
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
