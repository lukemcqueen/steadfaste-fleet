#!/usr/bin/env python3
"""free-model-ab2 — DISCRIMINATING head-to-head (fixes probe bug: 6th word IS 'over').

Adds tasks that separate a small flash model from the incumbents:
multi-constraint JSON, a runnable coding task, long-context needle retrieval,
multi-step arithmetic. Still deterministically graded (no LLM judge).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

ENV = Path.home() / ".hermes" / ".env"
CHAT = "https://openrouter.ai/api/v1/chat/completions"


def api_key() -> str:
    for line in ENV.read_text().splitlines():
        m = re.match(r"\s*(?:export\s+)?OPENROUTER_API_KEY\s*=\s*(.*)$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


def call(model, messages, key, tools=None, max_tokens=800):
    payload = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    req = urllib.request.Request(CHAT, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "steadfaste-free-ab"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:160]}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
    return {"content": msg.get("content") or "", "tool_calls": msg.get("tool_calls") or [],
            "usage": data.get("usage") or {}}


# ---------------- checkers ----------------
def chk_json(r):
    try:
        return json.loads(r["content"].strip()) == {"sum": 42}
    except Exception:  # noqa: BLE001
        return False

def chk_multi(r):
    try:
        o = json.loads(r["content"].strip())
        return o.get("a") == 3 and str(o.get("b")).lower() == "ananab"
    except Exception:  # noqa: BLE001
        return False

def chk_multistep(r):
    nums = re.findall(r"-?\d+", r["content"])
    return bool(nums) and nums[0] == "12"

def chk_needle(r):
    return "purple-otter-77" in r["content"].lower()

def chk_prime(r):
    code = re.sub(r"^```[a-zA-Z]*\n?|```$", "", r["content"].strip(), flags=re.M).strip()
    if not code:
        return False
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code + "\nprint([is_prime(n) for n in (2,4,17,1,0,97)])\n")
        path = f.name
    try:
        p = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=20)
        return p.stdout.strip() == "[True, False, True, False, False, True]"
    except Exception:  # noqa: BLE001
        return False
    finally:
        Path(path).unlink(missing_ok=True)


NEEDLE = ("Field notes from the expedition log. " * 40) + \
    " The recovered specimen tag reads purple-otter-77 and was catalogued at dusk. " + \
    ("Subsequent pages record routine weather and supply counts. " * 40)

TASKS = [
    ("json_format", [{"role": "user", "content": 'Respond with ONLY a JSON object, no prose/flags: {"sum": <n>} where n=17+25.'}], chk_json),
    ("multi_json", [{"role": "user", "content":
        'Return ONLY JSON with two keys: "a" = number of vowels in the word banana, '
        '"b" = the word banana reversed. No prose.'}], chk_multi),
    ("multistep", [{"role": "user", "content":
        'If x=3, y=x^2, z=y-x, what is z*2? Reply with only the number.'}], chk_multistep),
    ("needle_1M", [{"role": "user", "content": NEEDLE + "\n\nQuestion: what is the specimen tag? Reply with only the tag."}], chk_needle),
    ("coding_prime", [{"role": "user", "content":
        'Write a Python function is_prime(n) returning True iff n is prime. '
        'Output ONLY the code, no fences, no explanation.'}], chk_prime),
]

TOOLS = [{"type": "function", "function": {
    "name": "get_weather", "description": "Get current weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                   "required": ["city"]}}}]


def main() -> int:
    key = api_key()
    if not key:
        return 3
    models = [
        ("inclusionai/ling-3.2-flash:free", "CHALLENGER free"),
        ("openai/gpt-oss-20b", "incumbent simple head"),
        ("deepseek/deepseek-v4-flash-0731", "incumbent medium head"),
    ]
    out_rows = {}
    for mid, role in models:
        row = {"role": role, "tasks": {}, "tool_call": None, "err": None}
        for name, msgs, chk in TASKS:
            r = call(mid, msgs, key)
            if r.get("error"):
                row["tasks"][name] = "ERR"
                row["err"] = r["error"]
            else:
                row["tasks"][name] = "PASS" if chk(r) else "FAIL"
        tr = call(mid, [{"role": "user", "content": "Weather in Seoul right now?"}], key, tools=TOOLS)
        row["tool_call"] = "ERR" if tr.get("error") else ("CALLS_TOOL" if tr.get("tool_calls") else "NO_TOOL_CALL")
        out_rows[mid] = row

    print(f"{'model':<40} {'role':<24} score  tool-calling")
    print("-" * 88)
    for mid, row in out_rows.items():
        if row.get("err"):
            print(f"{mid:<40} {row['role']:<24} ERR {row['err'][:40]}")
            continue
        p = sum(1 for v in row["tasks"].values() if v == "PASS")
        print(f"{mid:<40} {row['role']:<24} {p}/{len(TASKS)}    {row['tool_call']}")
    print("\nper-task:")
    for mid, row in out_rows.items():
        if row.get("err"):
            continue
        print(f"  {mid}: " + ", ".join(f"{k}={v}" for k, v in row["tasks"].items()))
    p = Path.home() / ".hermes/cache/scratch/free-model-ab2.json"
    p.write_text(json.dumps(out_rows, indent=2) + "\n")
    print(f"\nraw -> {str(p).replace(str(Path.home()), '<HOME>')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
