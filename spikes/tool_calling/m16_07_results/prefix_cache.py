"""M16-07: does a shared ~5.4k-token system prompt get reused across chats?

Sends N chats that share one long system prompt but differ in the user
message, and prints the server's prompt-eval token count for each. With
working cross-chat reuse, only the first chat should prefill the system
prompt. Hits the model directly (no agent-server).

    BASE_URL=http://<model-candidate-ip>:8080 python3 prefix_cache.py
"""

import json
import os
import time
import urllib.request

BASE_URL = os.environ["BASE_URL"].rstrip("/")
N = int(os.environ.get("N", "3"))
SYSTEM = "You are a helpful assistant for a household server.\n" + "\n".join(
    f"Rule {i}: when asked about item {i}, answer with its number, {i * 7 % 101}, and nothing else."
    for i in range(1, 260)
)


def chat(user: str) -> dict:
    body = {
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        "max_tokens": 8,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        f"{BASE_URL}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    t = time.monotonic()
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.load(r)
    out["_wall_s"] = time.monotonic() - t
    return out


for i in range(N):
    r = chat(f"Chat {i}: what is item {i + 3}?")
    tm = r.get("timings", {})
    print(
        json.dumps(
            {
                "chat": i,
                "prompt_tokens": r["usage"]["prompt_tokens"],
                "prefilled": tm.get("prompt_n"),
                "cached": tm.get("cache_n"),
                "prefill_ms": round(tm.get("prompt_ms", 0)),
                "wall_s": round(r["_wall_s"], 1),
            }
        )
    )
