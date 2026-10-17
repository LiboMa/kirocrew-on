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


def version_accepted(version, extra):
    return version in SUPPORTED_VERSIONS or version in extra


def disable_live_target_reexec(environ):
    """Set KiroCrew's own exec-chain marker so a live-target pointer can never execve away the composition."""
    try:
        from kiro_crew.service.live_target import EXEC_MARKER
    except ImportError:
        return False
    environ[EXEC_MARKER] = "1"
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    parser.add_argument("--check", action="store_true", help="构建每个 poc-* 后端的 factory 并退出")
    parser.add_argument("--legacy-pipeline", action="store_true", help="同时写入旧版 poc-pipeline-* 模板")
    args = parser.parse_args(argv)
    settings = load(args.config)
    environment.prepare(settings, os.environ)
    factory_marker = settings.gateway_runtime_file.with_name("gateway-factory.json")
    factory_marker.unlink(missing_ok=True)
    if not disable_live_target_reexec(os.environ):
        print("注意：此 KiroCrew 版本没有 live-target 机制", file=sys.stderr)
    from kiro_crew._ssl_compat import _ensure_ssl_certs
    _ensure_ssl_certs()
    from kiro_crew import __version__
    from kiro_crew.config import KiroCrewConfig
    from kiro_crew.platform import assert_security_floor, boot_platform, set_context
    if not version_accepted(__version__, settings.kirocrew_versions):
        if not args.check:
            raise SystemExit(f"KiroCrew {__version__} 不在已验证集合中；先运行 --check 与原生契约测试，"
                             f"通过后在边界配置 kirocrew_versions 中显式接受")
        print(f"注意：KiroCrew {__version__} 尚未验证，本次仅做契约检查", file=sys.stderr)
    templates.seed(crew_home=settings.crew_home, kiro_home=settings.kiro_home,
                   allowed_roots=[settings.worker_data_dir],
                   legacy_pipeline=args.legacy_pipeline, yolo=settings.crew_yolo)
    cfg = KiroCrewConfig.load()
    if cfg.agent.session_sharing or cfg.session.pool_size:
        raise SystemExit("组合要求 session_sharing=false 且 pool_size=0；请检查 KIROCREW_HOME/config.json")
    ctx = boot_platform(cfg)
    composed = dataclasses.replace(ctx, providers=PocProviderRegistry(ctx.providers, settings.routing_log, factory_marker))
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
