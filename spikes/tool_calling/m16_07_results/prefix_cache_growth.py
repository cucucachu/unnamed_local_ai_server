"""#313: does a new chat reuse the cached prefix after another chat has grown?

Replays the agent's real first request (tools + system prompt, dumped from
agent-server's tests to REQ) as chat A, grows chat A by a few thousand tokens
like a tool-heavy turn would, then starts chat B with the same prefix and a
different user message. Prints prefilled/cached token counts per request.

    BASE_URL=http://<model-runner-ip>:8080 REQ=/tmp/req313.json python3 prefix_cache_growth.py
"""

import json
import os
import urllib.request

BASE_URL = os.environ["BASE_URL"].rstrip("/")
first = json.load(open(os.environ["REQ"]))[0]
TOOLS, SYSTEM = first["tools"], first["messages"][0]
FILLER = "\n".join(
    f"line {i}: the quick brown fox jumps over the lazy dog {i * 31 % 97}"
    for i in range(220)
)


def post(label: str, messages: list[dict]) -> None:
    body = {
        "messages": messages,
        "tools": TOOLS,
        "max_tokens": 4,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        f"{BASE_URL}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=900) as r:
        t = json.load(r)["timings"]
    print(
        json.dumps(
            {
                "req": label,
                "prefilled": t["prompt_n"],
                "cached": t.get("cache_n"),
                "prefill_ms": round(t["prompt_ms"]),
            }
        )
    )


chat_a = [
    SYSTEM,
    {"role": "user", "content": "Make me a grocery list app in my personal space."},
]
post("A1", chat_a)
for i in range(3):
    chat_a += [
        {"role": "assistant", "content": f"Reading part {i}."},
        {"role": "user", "content": f"Tool output {i}:\n{FILLER}"},
    ]
    post(f"A{i + 2}", chat_a)
for i in range(int(os.environ.get("INTERLEAVE", "0"))):
    other = {"role": "system", "content": f"Unrelated prompt {i}.\n{FILLER}"}
    post(f"X{i}", [other, {"role": "user", "content": "hi"}])
post(
    "B1",
    [
        SYSTEM,
        {"role": "user", "content": "What's the weather like for a picnic tomorrow?"},
    ],
)
post(
    "C1",
    [SYSTEM, {"role": "user", "content": "Summarise my notes from last week, please."}],
)
