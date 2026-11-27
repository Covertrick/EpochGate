"""Bounded model proposal adapter for managed receiver resources."""

import json
import time
from urllib.request import Request, urlopen

from epochgate_runtime.agent_decision import order_plan
from epochgate_runtime.agent_loop import api_key


class ModelCredentialMissing(RuntimeError):
    """Raised before sending a provider request."""


def propose(task, resources, *, timeout=45):
    credential = api_key()
    if not credential:
        raise ModelCredentialMissing("DeepSeek credential is not configured")
    prompt = (
        "Return JSON only with an object containing plan, a DAG of controlled resource writes. "
        "Nodes have step_id, dependencies, read_set, preconditions, and effect (resource to JSON value). "
        "Use only supplied resource names. Use one short node per effect. "
        "Tool syntax example: "
        '{"plan":[{"step_id":"publish","dependencies":[],"read_set":[],"preconditions":{},"effect":{"allowed_resource":"content"}}]}'
    )
    payload = {"model": "deepseek-flash", "thinking": {"type": "disabled"},
               "max_tokens": 700, "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": prompt},
                            {"role": "user", "content": json.dumps({"task": task, "resources": resources}, ensure_ascii=False)}]}
    request = Request("https://api.deepseek.com/chat/completions", data=json.dumps(payload).encode(),
                      headers={"Authorization": "Bearer " + credential, "Content-Type": "application/json"})
    started = time.perf_counter()
    with urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    plan = order_plan(json.loads(result["choices"][0]["message"]["content"])["plan"])
    allowed = set(resources)
    for node in plan:
        for source in (node, node.get("repair", {})):
            referenced = set(source.get("effect", {})) | set(source.get("preconditions", {})) | set(source.get("read_set", ()))
            if not referenced <= allowed:
                raise ValueError("Model proposal referenced resources outside its scope")
    usage = result["usage"]
    return {"plan": plan, "planner": {"model": result["model"], "tokens": usage["total_tokens"],
                                      "input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"],
                                      "elapsed_seconds": time.perf_counter() - started}}
