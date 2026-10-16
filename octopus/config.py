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
         "log_dir", "gateway_runtime_file", "kirocrew_python", "adapters_dir", "origin_secret_file", "worker_ca_file",
         "credentials_key_file"}
INTS = {"api_port": (1, 65535), "worker_port": (1, 65535), "gateway_port": (1, 65535), "engine_workers": (1, 32),
        "preview_tls_port": (1, 65535)}
FLOATS = {"retry_unavailable": (0.0, 600.0), "shutdown_grace": (0.0, 300.0)}
STRINGS = {"api_host", "worker_host", "public_url", "crew_url", "worker_isolation", "approval_mode", "crew_entry_ttl",
           "preview_tls_name", "preview_url", "preview_domain", "default_admission"}
APPROVAL = ("", "auto", "interactive")
BOOLS = {"crew_yolo"}
# Lifetime of the native browser login minted for "open in KiroCrew": 1–480 minutes or 1–8 hours (K03).
ENTRY_TTL = re.compile(r"(?:[1-9]|[1-9][0-9]|[1-3][0-9]{2}|4[0-7][0-9]|480)m|[1-8]h")
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
    # Self-hosted Git instances on a private network that repository credentials may name (D35; others are refused).
    credentials_private_hosts: tuple[str, ...] = ()
    public_url: str = ""
    # The remote preview's own distribution (D36 §5.2), e.g. https://dxxxx.cloudfront.net; empty: no remote preview.
    preview_url: str = ""
    preview_domain: str = ""   # D36 phase 3: each Session's preview at https://<run-id>.<preview_domain>/ (wildcard certificate)
    engine_workers: int = 4
    retry_unavailable: float = 2.0
    shutdown_grace: float = 30.0
    worker_host: str = "127.0.0.1"
    worker_port: int = 8928
    worker_isolation: str = "trusted-local"
    gateway_port: int = 5476
    crew_url: str = ""
    # KiroCrew native tool/spawn approval for this boundary; "" keeps the legacy seed default.
    approval_mode: str = ""
    default_admission: str = "auto"   # D44: a Session without `admission` runs at once; "confirm" waits for its plan's confirmation
    # KiroCrew's standing YOLO on this boundary (agent.dangerously_skip_permissions, re-applied at every Gateway start):
    # on by the owner's direction (2026-10-15); false returns to approving each tool request (D38 phase 2 then has work).
    crew_yolo: bool = True
    crew_entry_ttl: str = "8h"
    # A Worker host's nginx preview port and the name its certificate carries (D36 §5.2); unset on the platform host.
    preview_tls_port: int = 0
    preview_tls_name: str = ""
    kirocrew_python: Path | None = None
    adapters_dir: Path | None = None
    # Secret CloudFront adds as X-Origin-Verify; when set, only verified (or host-local) requests are served.
    origin_secret_file: Path | None = None
    # Private CA that signs remote Worker hosts' TLS certificates (D28); the platform trusts only it for HTTPS Workers.
    worker_ca_file: Path | None = None
    # Master key of the sealed repository credentials (D35 §3.3); unset: <data_dir>/credentials.key, created on first use.
    credentials_key_file: Path | None = None
    path_prepend: tuple[Path, ...] = ()
    # KiroCrew releases accepted beyond the verified set, only after their contract check passed.
    kirocrew_versions: tuple[str, ...] = ()
    tools: dict = field(default_factory=dict)


def _path(value, base):
    path = Path(os.path.expandvars(str(value))).expanduser()
    return path if path.is_absolute() else base / path


def _layer(values, base):
    """Validate one layer's raw values and resolve its relative paths against ``base``."""
    known = PATHS | set(INTS) | set(FLOATS) | STRINGS | BOOLS | {"allowed_hosts", "path_prepend", "tools", "kirocrew_versions",
                                                         "credentials_private_hosts"}
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
        elif key in BOOLS:
            words = {"true": True, "1": True, "yes": True, "on": True, "false": False, "0": False, "no": False, "off": False}
            if isinstance(value, bool):
                result[key] = value
            elif isinstance(value, str) and value.strip().lower() in words:
                result[key] = words[value.strip().lower()]
            else:
                raise ConfigError(f"{key} 必须为 true 或 false")
        elif key in ("allowed_hosts", "credentials_private_hosts"):
            hosts = tuple(h.strip() for h in (value.split(",") if isinstance(value, str) else value) if h.strip())
            if not all(HOST.match(h) for h in hosts):
                raise ConfigError(f"{key} 只能包含 host[:port]")
            result[key] = hosts
        elif key == "kirocrew_versions":
            versions = tuple(v.strip() for v in (value.split(",") if isinstance(value, str) else value) if str(v).strip())
            if not all(re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", v) for v in versions):
                raise ConfigError("kirocrew_versions 只能包含版本号")
            result[key] = versions
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
            if key == "approval_mode" and value not in APPROVAL:
                raise ConfigError("approval_mode 只能是 auto 或 interactive（留空表示不指定）")
            if key == "default_admission" and value not in ("auto", "confirm"):
                raise ConfigError("default_admission 只能是 auto 或 confirm")
            if key == "crew_entry_ttl" and not ENTRY_TTL.fullmatch(value):
                raise ConfigError("crew_entry_ttl 只能是 1–480m 或 1–8h")
            if key == "preview_url" and value and not re.fullmatch(r"https?://[A-Za-z0-9.\-]+(:\d{1,5})?/?", value):
                raise ConfigError("preview_url 只能是 https://<域名>[:端口]")
            if key == "preview_domain" and value and not re.fullmatch(
                    r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", value):
                raise ConfigError("preview_domain 只能是小写域名，如 preview.example.com（会话的预览在 <run-id>.<preview_domain>）")
            result[key] = value
    return result


def _environment(environ):
    # Only known settings are read: the OCTOPUS_ namespace is shared with secrets passed by name
    # (e.g. `init --password-env OCTOPUS_ADMIN_PASSWORD`). Unknown keys are still errors in files.
    known = PATHS | set(INTS) | set(FLOATS) | STRINGS | BOOLS | {"allowed_hosts", "path_prepend", "kirocrew_versions",
                                                         "credentials_private_hosts"}
    values, tools = {}, {}
    for key, value in environ.items():
        if key.startswith("OCTOPUS_TOOL_"):
            tools[key.removeprefix("OCTOPUS_TOOL_").lower()] = value
        elif key.startswith("OCTOPUS_") and key.removeprefix("OCTOPUS_").lower() in known:
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
