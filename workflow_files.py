"""Portable workflow documents. No execution, persistence or model calls."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import sys


def yaml_module():
    try:
        import yaml
        return yaml
    except ImportError:
        # Reuse the installed Crew application's PyYAML (pure-Python fallback
        # works across Python versions). Other installations use requirements.txt.
        package = Path("/Applications/KiroCrew.app/Contents/Resources/backend-dist/"
                       "kirocrew-backend-arm64/lib/python3.12/site-packages/yaml")
        if not (package / "__init__.py").is_file():
            raise ValueError("YAML 需要 PyYAML；请安装 requirements-workflow.txt 中的依赖。") from None
        spec = importlib.util.spec_from_file_location(
            "yaml", package / "__init__.py", submodule_search_locations=[str(package)])
        module = importlib.util.module_from_spec(spec)
        sys.modules["yaml"] = module
        spec.loader.exec_module(module)
        return module


def parse_document(content, format):
    if not isinstance(content, str) or not 0 < len(content.encode()) <= 100000:
        raise ValueError("工作流文件应为非空 UTF-8 文本，最大 100 KB。")
    format = format.lower()
    if format in {"md", "markdown"}:
        blocks = re.findall(r"^(`{3,})(yaml|yml|json|workflow)\s*\n(.*?)^\1\s*$",
                            content, re.M | re.S)
        if len(blocks) != 1:
            raise ValueError("Markdown 必须包含一个完整的 yaml 或 json 工作流代码块。")
        _, format, content = blocks[0]
        if format == "workflow":
            format = "yaml"
    try:
        if format == "json":
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("重复字段：" + key)
                    result[key] = value
                return result
            return json.loads(content, object_pairs_hook=unique)
        if format not in {"yaml", "yml"}:
            raise ValueError("支持 JSON、YAML、Markdown 工作流文件。")
        yaml = yaml_module()

        class Loader(yaml.SafeLoader):
            def compose_node(self, parent, index):
                if self.check_event(yaml.AliasEvent):
                    raise ValueError("请展开 YAML 锚点引用后导入。")
                return super().compose_node(parent, index)

        def mapping(loader, node):
            result = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node)
                if not isinstance(key, str) or key in result:
                    raise ValueError("YAML 字段必须是唯一的字符串。")
                result[key] = loader.construct_object(value_node)
            return result

        Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        return yaml.load(content, Loader=Loader)
    except (RecursionError, TypeError) as exc:
        raise ValueError("文件结构无效或嵌套过深。") from exc


def emit_document(definition, format):
    format = format.lower()
    if format == "json":
        return json.dumps(definition, ensure_ascii=False, indent=2) + "\n"
    if format not in {"yaml", "yml", "md", "markdown"}:
        raise ValueError("支持 JSON、YAML、Markdown 工作流文件。")
    content = yaml_module().safe_dump(definition, allow_unicode=True, sort_keys=False)
    if format in {"yaml", "yml"}:
        return content
    # A fence longer than any user-authored backtick run is round-trip safe.
    fence = "`" * max(3, max((len(s) + 1 for s in re.findall(r"`+", content)), default=3))
    steps = "\n".join(f"- `{s['id']}`：{s['name']} / {s['tool']} / "
                      f"{s['model']} / Effort {s['effort'] or '默认'}"
                      for s in definition["steps"])
    return (f"# {definition['name']}\n\n工作流 `{definition['id']}`。"
            "修改后导入为草稿，保存新版本后下次运行生效。\n\n"
            f"{steps}\n\n## 可导入配置\n\n{fence}yaml\n{content}{fence}\n")
