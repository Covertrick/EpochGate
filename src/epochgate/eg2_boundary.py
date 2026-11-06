"""Agent 容器环境里不能出现模型密钥。测试只检查名字，不记录值。"""

from __future__ import annotations

SECRET_NAMES = ("DEEPSEEK_API_KEY", "EPOCHGATE_API_KEY", "DS_API_KEY")


def agent_process_env(parent: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in parent.items() if key not in SECRET_NAMES}


def container_exec_args(container: str, argv: list[str]) -> list[str]:
    return ["docker", "exec", "-i", "-u", "1000:1000", "-w", "/workspace", container, *argv]


def command_refused(command: str) -> bool:
    text = command.lower()
    markers = (".env", "gold", "deepseek", "epochgate_api", "git push")
    return any(marker in text for marker in markers)
