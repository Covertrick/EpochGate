"""确认 DeepSeek 别名能返回响应。不打印密钥。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    _load_env(Path(".env"))
    key = os.environ.get("DS_API_KEY") or os.environ.get("EPOCHGATE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        print("api_error missing_key")
        raise SystemExit(1)
    base = os.environ.get("EPOCHGATE_BASE_URL", "https://api.deepseek.com").rstrip("/")
    if "api.deepseek.com" not in base:
        base = "https://api.deepseek.com"
    url = base + "/chat/completions"
    body = {
        "model": "deepseek-flash",
        "messages": [{"role": "user", "content": "Reply with exactly: API_OK"}],
        "thinking": {"type": "disabled"},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"api_error http_{exc.code}")
        raise SystemExit(1) from exc
    except urllib.error.URLError as exc:
        print(f"api_error {type(exc.reason).__name__}")
        raise SystemExit(1) from exc
    message = payload["choices"][0]["message"].get("content") or ""
    reported = payload.get("model", "")
    status = "API_OK" if message.strip() == "API_OK" else "api_error unexpected_content"
    print(json.dumps({"status": status, "reported_model": reported}, ensure_ascii=False))
    if status != "API_OK":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
