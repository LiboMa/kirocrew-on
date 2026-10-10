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
