"""Minimal YAML for the manifest subset this package writes.

Supports block mappings, nested mappings, and lists of scalars.
"""

from __future__ import annotations

import json
from typing import Any


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    text = str(value)
    if text == "" or any(ch in text for ch in ":#{}[]&*!|>%@`,\"'") or text.lower() in {"null", "true", "false", "~"}:
        return json.dumps(text, ensure_ascii=False)
    return text


def dump(value: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}{key}:")
                lines.append(dump(item, indent + 1))
            elif isinstance(item, dict):
                lines.append(f"{pad}{key}: {{}}")
            elif isinstance(item, list):
                lines.append(f"{pad}{key}: []")
            else:
                lines.append(f"{pad}{key}: {_scalar(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                raise TypeError("lists of collections are not emitted")
            lines.append(f"{pad}- {_scalar(item)}")
        return "\n".join(lines)
    return f"{pad}{_scalar(value)}"


def _parse_scalar(text: str) -> Any:
    text = text.strip()
    if text in {"", "null", "~"}:
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if text == "{}":
        return {}
    if text == "[]":
        return []
    if text.startswith('"') or text.startswith("'"):
        return json.loads(text) if text.startswith('"') else text[1:-1]
    try:
        return int(text)
    except ValueError:
        return text


def loads(text: str) -> Any:
    lines = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, raw.strip()))
    index = 0

    def parse_block(indent: int) -> Any:
        nonlocal index
        if index >= len(lines):
            return None
        if lines[index][1].startswith("- "):
            items = []
            while index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
                items.append(_parse_scalar(lines[index][1][2:]))
                index += 1
            return items
        mapping: dict[str, Any] = {}
        while index < len(lines) and lines[index][0] == indent and not lines[index][1].startswith("- "):
            key, _, rest = lines[index][1].partition(":")
            index += 1
            rest = rest.strip()
            if rest == "":
                if index < len(lines) and lines[index][0] > indent:
                    mapping[key] = parse_block(lines[index][0])
                else:
                    mapping[key] = None
            else:
                mapping[key] = _parse_scalar(rest)
        return mapping

    if not lines:
        return None
    return parse_block(lines[0][0])


def load_path(path: Any) -> Any:
    return loads(path.read_text(encoding="utf-8"))
