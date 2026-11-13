#!/usr/bin/env bash
# Worker (model x effort) A/B - 2026-11-13, cycle 8258.
# Question from the operator: could a different/cheaper model at MEDIUM beat
# deepseek-flash at HIGH on the WORKER, or is flash at LOW sufficient?
# Grades each (model, effort) pair on representative worker tasks for BOTH
# correctness (graded by a fixed rubric) and token cost. MEASUREMENT ONLY.
exec python3 - <<'PY'
import json, os, pathlib, time, urllib.error, urllib.request

HERE = pathlib.Path("docs/evidence/worker-model-effort-ab-2026-11-13")
HERE.mkdir(parents=True, exist_ok=True)


def key():
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    for line in (pathlib.Path.home() / ".hermes" / ".env").read_text().splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "OPENROUTER_API_KEY":
            return value.strip().strip('"').strip("'")
    raise SystemExit("no OPENROUTER_API_KEY")


def call(model, user_prompt, effort):
    body = {"model": model, "temperature": 0,
            "messages": [{"role": "user", "content": user_prompt}]}
    if effort:
        body["reasoning_effort"] = effort
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer %s" % key(),
                 "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"status": "refused", "http": e.code, "detail": e.read().decode()[:200]}
    usage = payload.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    out = payload.get("choices") or [{}]
    text = (out[0].get("message") or {}).get("content") or ""
    return {"status": "ok", "latency_s": round(time.time() - t0, 1),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens"),
            "content": text}


# Representative worker tasks, each with a checkable answer.
TASKS = [
    ("sql", "Given a table orders(id, customer_id, amount, created_at), write "
            "one SQL query returning total sales per customer for 2026, ordered "
            "highest first, only customers with > $1000. Return ONLY the SQL."),
    ("tools-route", "You have a file at /tmp/data.csv. Do you need a tool to "
            "read it? Answer 'read_file' / 'terminal' / 'no tool needed' in one word."),
    ("extract", "From: 'Order #4821 shipped 2026-11-10 to bob@example.com, total "
            "$4,276.55'. Extract the order id, date, and total as JSON."),
]

# Grade rubric (applied to the text, tolerant).
def grade(task, text):
    t = text.lower()
    if task == "sql":
        return ("select" in t and "group by" in t and "having" in t and "2026" in t)
    if task == "tools-route":
        return any(w in t for w in ("read_file", "terminal", "no tool needed"))
    if task == "extract":
        return ("4821" in t and "2026-11-10" in t and ("4276" in t or "4,276.55" in t))
    return False

CANDIDATES = [
    ("deepseek/deepseek-v4-flash-0731", "high",   "current worker @high (baseline)"),
    ("deepseek/deepseek-v4-flash-0731", "low",    "current worker @low (proposed)"),
    ("deepseek/deepseek-v4-flash-0731", "medium", "current worker @medium"),
    ("xiaomi/mimo-v2.6-pro",            "medium", "mimo @medium (6x cheaper than pin)"),
    ("qwen/qwen3-coder-30b-a3b-instruct", "medium", "qwen31c @medium"),
]

PRICE = {
    "deepseek/deepseek-v4-flash-0731": (0.119, 0.238),
    "xiaomi/mimo-v2.6-pro": (0.435, 0.87),
    "qwen/qwen3-coder-30b-a3b-instruct": (0.07, 0.28),
}

lines = []
for model, effort, tag in CANDIDATES:
    for tname, prompt in TASKS:
        r = call(model, prompt, effort)
        out = {"model": model, "effort": effort, "tag": tag, "task": tname,
               "status": r.get("status"), "latency_s": r.get("latency_s"),
               "prompt_tokens": r.get("prompt_tokens"),
               "completion_tokens": r.get("completion_tokens"),
               "reasoning_tokens": r.get("reasoning_tokens")}
        if r.get("status") == "ok":
            ok = grade(tname, r["content"])
            pi, po = PRICE.get(model, (0.1, 0.3))
            cost = ((r.get("prompt_tokens") or 0) / 1e6 * pi +
                    (r.get("completion_tokens") or 0) / 1e6 * po)
            out.update({"correct": ok, "cost_usd": round(cost, 6),
                        "answer_preview": r["content"][:120].replace("\n", " ")})
        lines.append(out)
        print(json.dumps(out, sort_keys=True))

(HERE / "raw.jsonl").write_text(
    "\n".join(json.dumps(x, sort_keys=True) for x in lines) + "\n")

# Summarize per (model,effort)
print()
print("=== SUMMARY: correct/total, and cost ===")
from collections import defaultdict
agg = defaultdict(lambda: {"ok": 0, "n": 0, "cost": 0.0})
for r in lines:
    k = (r["model"], r["effort"])
    agg[k]["n"] += 1
    agg[k]["ok"] += 1 if r.get("correct") else 0
    agg[k]["cost"] += r.get("cost_usd", 0.0)
for (m, e), a in sorted(agg.items()):
    print(f"  {m:38s} {e:6s}  correct={a['ok']}/{a['n']}  total_cost=${a['cost']:.6f}")
print("Saved: " + str(HERE / "raw.jsonl"))
PY
