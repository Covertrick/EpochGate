"""按当前检出生成 DSH 插件路径，不把某台机器的绝对路径写进仓库。"""

from __future__ import annotations

from pathlib import Path

_PLUGINS = ("eg-workspace", "eg-policy", "eg-observability")


def find_repo(start: Path | None = None) -> Path | None:
    here = Path.cwd() if start is None else start
    for parent in [here, *here.parents]:
        if (parent / "packages" / "eg-workspace" / "lib" / "index.js").is_file():
            return parent
    return None


def plugin_index(repo: Path, name: str) -> Path:
    return repo / "packages" / name / "lib" / "index.js"


def dsh_binary(repo: Path) -> Path:
    return repo / "infrastructure" / "dsh-profiles" / "research" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"


def research_profile(repo: Path) -> Path:
    return repo / "infrastructure" / "dsh-profiles" / "research"


def render_plugin_patch(repo: Path) -> str:
    missing = [name for name in _PLUGINS if not plugin_index(repo, name).is_file()]
    if missing:
        raise FileNotFoundError("missing plugins: " + ", ".join(missing))
    inserts = "\n".join(
        f'    - id: {name}\n      name: "{plugin_index(repo, name).resolve().as_uri()}"'
        for name in _PLUGINS
    )
    return (
        "# Generated for this checkout. Host shells stay closed.\n"
        "- id: session-log-deepseek\n"
        '  name: "@deepseek-ai/dsh-session-log-deepseek"\n'
        "  config:\n"
        "    enabled: false\n"
        "- id: agent-default-model\n"
        '  name: "@deepseek-ai/dsh-agent-default-model"\n'
        "  config:\n"
        "    provider: deepseek-official\n"
        "    model: deepseek-flash\n"
        "- id: tool-bash\n"
        "  disabled: true\n"
        "- id: tool-pwsh\n"
        "  disabled: true\n"
        "- id: bash-sandbox\n"
        "  disabled: true\n"
        "- id: pwsh-sandbox\n"
        "  disabled: true\n"
        "- id: tool-jobs\n"
        "  disabled: true\n"
        "- id: tool-fs\n"
        "  disabled: true\n"
        "- id: tool-fs-search\n"
        "  disabled: true\n"
        "- id: tool-plugin-manager\n"
        "  disabled: true\n"
        "- id: tool-web\n"
        "  disabled: true\n"
        "- insert:\n"
        f"{inserts}\n"
    )


def write_plugin_patch(repo: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = render_plugin_patch(repo)
    if "D:/Workspace/EpochGate" in text:
        raise RuntimeError("plugin patch still points at a foreign checkout")
    destination.write_text(text, encoding="utf-8")
    return destination
