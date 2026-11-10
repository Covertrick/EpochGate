"""DeepSeek chat completion. Prints and returns no API key."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def api_key() -> str:
    load_env()
    return os.environ.get("DS_API_KEY") or os.environ.get("EPOCHGATE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""


def _redact(text: str, key: str) -> str:
    if key and key in text:
        return text.replace(key, "[redacted]")
    return text


def chat(content: str, *, model: str, max_tokens: int = 256) -> dict[str, Any]:
    """请求指定模型。模型名被拒绝时不改用别的模型。"""
    key = api_key()
    if not key:
        return {"ok": False, "error": "missing_key", "requested_model": model}
    base = os.environ.get("EPOCHGATE_BASE_URL", "https://api.deepseek.com").rstrip("/")
    urls = [base + "/chat/completions"]
    if not base.endswith("/v1"):
        urls.append(base + "/v1/chat/completions")
    thinking_modes: list[dict[str, Any] | None] = [{"type": "disabled"}, None]
    last_error = "unreachable"
    used_url = urls[0]
    for url in urls:
        for thinking in thinking_modes:
            body: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0,
                "max_tokens": max_tokens,
            }
            if thinking is not None:
                body["thinking"] = thinking
            request = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", "replace")
                last_error = _redact(f"http_{exc.code}: {raw[:400]}", key)
                used_url = url
                if exc.code == 404:
                    break
                if exc.code == 400 and thinking is not None and "thinking" in raw.lower():
                    continue
                return {
                    "ok": False,
                    "error": last_error,
                    "requested_model": model,
                    "url": url,
                    "http_status": exc.code,
                }
            except urllib.error.URLError as exc:
                last_error = _redact(type(exc.reason).__name__, key)
                return {"ok": False, "error": last_error, "requested_model": model, "url": url}
            message = payload.get("choices", [{}])[0].get("message", {})
            text = message.get("content") or ""
            return {
                "ok": True,
                "requested_model": model,
                "reported_model": payload.get("model"),
                "content": _redact(text, key),
                "usage": payload.get("usage"),
                "url": url,
                "thinking": thinking,
            }
    return {"ok": False, "error": last_error, "requested_model": model, "url": used_url}
