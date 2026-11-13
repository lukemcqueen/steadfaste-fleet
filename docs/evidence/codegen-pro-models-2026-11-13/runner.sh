#!/usr/bin/env bash
# Code-generation A/B across the three pro-class hard-tier models - 2026-11-13, cycle 8275.
# Question from the operator: "hard turns should go to Mimo?" - the plan-REVIEW A/B
# proved mimo is the best reviewer (equal recall, ~6x cheaper, fastest), but that is a
# DEFECT-FINDING surface. Here we measure CODE GENERATION (the hard surface the operator
# is really asking about) across deepseek-v4-pro-0813 (hard/coding head), z-ai/glm-4.7
# (hard/coding #2) and xiaomi/mimo-v2.6-pro (the reviewer winner, tested for generation).
# Graded by EXECUTING the generated function against known-correct reference tests
# (outcomes > outputs), each run in a timeout-bounded child process so a non-terminating
# solution grades as wrong instead of hanging. MEASUREMENT ONLY.
exec python3 - <<'PY'
import json, os, pathlib, re, subprocess, sys, time, urllib.error, urllib.request

HERE = pathlib.Path("docs/evidence/codegen-pro-models-2026-11-13")
HERE.mkdir(parents=True, exist_ok=True)

def key():
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    for line in (pathlib.Path.home() / ".hermes" / ".env").read_text().splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "OPENROUTER_API_KEY":
            return value.strip().strip('"').strip("'")
    raise SystemExit("no OPENROUTER_API_KEY")

def call(model, user_prompt):
    body = {"model": model, "temperature": 0, "reasoning_effort": "medium",
            "messages": [{"role": "user", "content": user_prompt}]}
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer %s" % key(), "Content-Type": "application/json"})
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

# ── Task definitions: spec prompt + reference source + test vectors ────────
# Every task is a single pure function `f` (stdlib only). The reference is OUR
# known-correct implementation (the oracle); hidden tests compare the model's output
# to it. exec runs in the CHILD subprocess (sandbox boundary + kill-on-timeout).
TASKS = {}

TASKS["median"] = {
    "prompt": ("Write a Python function `f(nums1, nums2)` that returns the median "
        "of two sorted arrays as a float. nums1 and nums2 are sorted ascending lists "
        "of ints. Examples: f([1,3],[2]) -> 2.0 ; f([1,2],[3,4]) -> 2.5. "
        "Return ONLY the function definition (no markdown fences, no explanation, "
        "no imports). Name it exactly `f`."),
    "mode": "compare",
    "ref_src": (
        "import heapq\n"
        "def f_ref(nums1, nums2):\n"
        "    m = list(heapq.merge(nums1, nums2))\n"
        "    L = len(m)\n"
        "    return float(m[L//2]) if L % 2 else (m[L//2-1]+m[L//2])/2.0\n"),
    "test_src": "f_ref",
    "tests": [
        ([1,3],[2]), ([1,2],[3,4]), ([],[1]), ([0,0],[0,0]), ([2],[]),
        ([1,5,9],[2,3,7,11]), ([-2,-1],[1,3])],
}

TASKS["trapping_rain_water"] = {
    "prompt": ("Write a Python function `f(height)` that returns how much water it "
        "can trap after raining, given a list of non-negative int bar heights. "
        "Examples: f([0,1,0,2,1,0,1,3,2,1,2,1]) -> 6 ; f([4,2,0,3,2,5]) -> 9. "
        "Return ONLY the function definition `f` (no markdown, no explanation)."),
    "mode": "compare",
    "ref_src": (
        "def f_ref(height):\n"
        "    n=len(height)\n"
        "    if n<3: return 0\n"
        "    l=[0]*n; r=[0]*n\n"
        "    m=0\n"
        "    for i in range(n): m=max(m,height[i]); l[i]=m\n"
        "    m=0\n"
        "    for i in range(n-1,-1,-1): m=max(m,height[i]); r[i]=m\n"
        "    return sum(min(l[i],r[i])-height[i] for i in range(n))\n"),
    "test_src": "f_ref",
    "tests": [
        ([0,1,0,2,1,0,1,3,2,1,2,1],), ([4,2,0,3,2,5],), ([],), ([3],),
        ([2,0,2],), ([5,4,1,2],), ([0,2,1,4,0,1],)],
}

TASKS["edit_distance"] = {
    "prompt": ("Write a Python function `f(word1, word2)` that returns the minimum "
        "number of operations (insert, delete, replace) to convert word1 to word2. "
        "Examples: f('horse','ros') -> 3 ; f('intention','execution') -> 5. "
        "Return ONLY the function definition `f` (no markdown, no explanation)."),
    "mode": "compare",
    "ref_src": (
        "def f_ref(a,b):\n"
        "    dp=[[0]*(len(b)+1) for _ in range(len(a)+1)]\n"
        "    for i in range(len(a)+1): dp[i][0]=i\n"
        "    for j in range(len(b)+1): dp[0][j]=j\n"
        "    for i in range(1,len(a)+1):\n"
        "        for j in range(1,len(b)+1):\n"
        "            dp[i][j]=min(dp[i-1][j]+1, dp[i][j-1]+1, dp[i-1][j-1]+(a[i-1]!=b[j-1]))\n"
        "    return dp[len(a)][len(b)]\n"),
    "test_src": "f_ref",
    "tests": [
        ("horse","ros"), ("intention","execution"), ("",""), ("a","a"),
        ("abc",""), ("flaw","lawn"), ("sunday","saturday")],
}

TASKS["longest_valid_parens"] = {
    "prompt": ("Write a Python function `f(s)` that returns the length of the longest "
        "valid (well-formed) parentheses substring. Examples: f(')()())') -> 4 ; "
        "f('((()))') -> 6 ; f('()(()') -> 2 ; f(')(') -> 0. "
        "Return ONLY the function definition `f` (no markdown, no explanation)."),
    "mode": "compare",
    "ref_src": (
        "def f_ref(s):\n"
        "    st=[-1]; best=0\n"
        "    for i,ch in enumerate(s):\n"
        "        if ch=='(': st.append(i)\n"
        "        else:\n"
        "            st.pop()\n"
        "            if st: best=max(best,i-st[-1])\n"
        "            else: st.append(i)\n"
        "    return best\n"),
    "test_src": "f_ref",
    "tests": [
        (")()())",), ("(()",), ("",), ("((()))",), (")())(()())",), ("()(())",),
        ("()(()",), (")(",), ("()())",)],
}

TASKS["atoi"] = {
    "prompt": ("Write a Python function `f(s)` that implements a string-to-integer "
        "converter per this spec: skip leading whitespace, optional sign, then digits; "
        "stop at the first non-digit; clamp the result to the 32-bit signed range "
        "[-2147483648, 2147483647]. If no digits, return 0. Examples: f('   -42') -> -42 ; "
        "f('4193 with words') -> 4193 ; f('words and 987') -> 0 ; f('-91283472332') -> -2147483648. "
        "Return ONLY the function definition `f` (no markdown, no explanation)."),
    "mode": "compare",
    "ref_src": (
        "def f_ref(s):\n"
        "    s=s.strip()\n"
        "    if not s: return 0\n"
        "    i=0; sign=1\n"
        "    if s[0] in '+-':\n"
        "        sign=-1 if s[0]=='-' else 1; i=1\n"
        "    num=0\n"
        "    while i<len(s) and s[i].isdigit():\n"
        "        num=num*10+int(s[i]); i+=1\n"
        "    if sign==1: return min(num, 2**31-1)\n"
        "    return max(-num, -2**31)\n"),
    "test_src": "f_ref",
    "tests": [
        ("42",), ("   -42",), ("4193 with words",), ("words and 987",),
        ("-91283472332",), ("2147483648",), ("",), ("  +0 123",),
        ("3.14",), ("-",), ("+1",), ("000",)],
}

TASKS["topo_order"] = {
    "prompt": ("Write a Python function `f(n, edges)` that returns a valid topological "
        "ordering of nodes 0..n-1 as a list, given edges as a list of (u, v) tuples meaning "
        "u must come before v. Any valid order is accepted. n == len of the node set. "
        "Examples: f(4, [(1,0),(2,0),(3,1),(3,2)]) could be [3,1,2,0]. Use Kahn's "
        "algorithm. Return ONLY the function definition `f` (no markdown, no explanation, "
        "you may import collections)."),
    "mode": "topo",
    "ref_src": (
        "from collections import deque\n"
        "def f_ref(n, edges):\n"
        "    indeg=[0]*n; adj=[[] for _ in range(n)]\n"
        "    for u,v in edges: adj[u].append(v); indeg[v]+=1\n"
        "    q=deque([i for i in range(n) if indeg[i]==0])\n"
        "    res=[]\n"
        "    while q:\n"
        "        u=q.popleft(); res.append(u)\n"
        "        for v in adj[u]:\n"
        "            indeg[v]-=1\n"
        "            if indeg[v]==0: q.append(v)\n"
        "    return res\n"),
    "test_src": None,
    "tests": [
        (4, [(1,0),(2,0),(3,1),(3,2)]),
        (6, [(0,1),(1,2),(2,3),(3,4),(4,5)]),
        (3, [(0,2),(1,2)]),
        (5, []),
        (2, [(1,0)]),
    ],
}

def make_harness(task):
    tests_repr = repr(task["tests"])
    if task["mode"] == "compare":
        refname = task["test_src"]
        return (
            "import json, sys\n"
            + task["ref_src"] + "\n"
            f"TESTS = {tests_repr}\n"
            "code = sys.stdin.read()\n"
            "ns = {}\n"
            "try:\n    exec(code, ns)\n"
            "except Exception as e:\n"
            "    print(json.dumps({'pass': False, 'reason': 'exec_error: ' + type(e).__name__ + ': ' + str(e)[:100]})); sys.exit(0)\n"
            "fn = ns.get('f')\n"
            "ref = " + refname + "\n"
            "if not callable(fn) or not callable(ref):\n"
            "    print(json.dumps({'pass': False, 'reason': 'no callable f (or ref)'})); sys.exit(0)\n"
            "fails = []\n"
            "for args in TESTS:\n"
            "    try: exp = ref(*args); got = fn(*args)\n"
            "    except Exception as e:\n"
            "        fails.append({'args': repr(args), 'error': type(e).__name__ + ': ' + str(e)[:80]}); continue\n"
            "    ok = (abs(got-exp) < 1e-9) if isinstance(exp, float) else (got == exp)\n"
            "    if not ok: fails.append({'args': repr(args), 'got': repr(got), 'exp': repr(exp)})\n"
            "print(json.dumps({'pass': len(fails) == 0, 'tested': len(TESTS), 'fails': fails}))\n"
        )
    # topo mode
    return (
        "import json, sys\n"
        f"TESTS = {tests_repr}\n"
        "code = sys.stdin.read()\n"
        "ns = {}\n"
        "try:\n    exec(code, ns)\n"
        "except Exception as e:\n"
        "    print(json.dumps({'pass': False, 'reason': 'exec_error: ' + type(e).__name__})); sys.exit(0)\n"
        "fn = ns.get('f')\n"
        "if not callable(fn):\n"
        "    print(json.dumps({'pass': False, 'reason': 'no callable f'})); sys.exit(0)\n"
        "fails = []\n"
        "for n, edges in TESTS:\n"
        "    try: res = fn(n, edges)\n"
        "    except Exception as e:\n"
        "        fails.append({'n': n, 'edges': repr(edges), 'error': type(e).__name__ + ': ' + str(e)[:80]}); continue\n"
        "    if not (isinstance(res, list) and len(res) == n and set(res) == set(range(n))):\n"
        "        fails.append({'n': n, 'reason': 'not a permutation of 0..n-1', 'res': repr(res)}); continue\n"
        "    idx = {v: i for i, v in enumerate(res)}\n"
        "    for u, v in edges:\n"
        "        if idx[u] >= idx[v]:\n"
        "            fails.append({'n': n, 'edge': [u, v], 'res': repr(res)}); break\n"
        "print(json.dumps({'pass': len(fails) == 0, 'tested': len(TESTS), 'fails': fails}))\n"
    )

def grade(generation, task):
    """Run the generated code against the task's reference tests in a bounded child."""
    harness = make_harness(task)
    code = generation["content"]
    m = re.search(r"```(?:python)?\s*\n(.*?)```", code, re.S)
    if m:
        code = m.group(1)
    try:
        p = subprocess.run([sys.executable, "-c", harness], input=code.encode(),
                           capture_output=True, timeout=25)
    except subprocess.TimeoutExpired:
        return {"pass": False, "reason": "timeout (25s)", "tested": None}
    try:
        verdict = json.loads(p.stdout.decode())
    except Exception:
        return {"pass": False, "reason": "unparseable grader stdout",
                "stderr": p.stderr.decode()[:120]}
    return verdict

CANDIDATES = [
    ("deepseek/deepseek-v4-pro-0813",   "hard/coding head (current)"),
    ("z-ai/glm-4.7",                    "hard/coding #2 (current)"),
    ("xiaomi/mimo-v2.6-pro",             "reviewer A/B winner - tested for generation"),
]
PRICE = {
    "deepseek/deepseek-v4-pro-0813": (0.66, 1.98),
    "z-ai/glm-4.7": (0.54, 1.98),
    "xiaomi/mimo-v2.6-pro": (0.435, 0.87),
}

lines = []
for model, tag in CANDIDATES:
    for tname in TASKS:
        task = TASKS[tname]
        r = call(model, task["prompt"])
        out = {"model": model, "tag": tag, "task": tname,
               "effort": "medium", "status": r.get("status"),
               "latency_s": r.get("latency_s"), "prompt_tokens": r.get("prompt_tokens"),
               "completion_tokens": r.get("completion_tokens"),
               "reasoning_tokens": r.get("reasoning_tokens")}
        if r.get("status") == "ok":
            g = grade(r, task)
            out["correct"] = g.get("pass")
            out["tests_run"] = g.get("tested")
            out["fail_detail"] = json.dumps(g.get("fails") or g.get("reason"))
            pi, po = PRICE.get(model, (0.5, 1.5))
            out["cost_usd"] = round(((r.get("prompt_tokens") or 0)/1e6*pi +
                                     (r.get("completion_tokens") or 0)/1e6*po), 6)
            out["answer_preview"] = r["content"][:100].replace("\n", " ")
        lines.append(out)
        print(json.dumps(out, sort_keys=True, default=str))

(HERE / "raw.jsonl").write_text("\n".join(json.dumps(x, sort_keys=True, default=str) for x in lines) + "\n")

print()
print("=== SUMMARY: correct/total, cost, latency ===")
from collections import defaultdict
agg = defaultdict(lambda: {"ok": 0, "n": 0, "cost": 0.0, "lat": []})
for r in lines:
    if r["status"] != "ok":
        continue
    agg[r["model"]]["n"] += 1
    agg[r["model"]]["ok"] += 1 if r.get("correct") else 0
    agg[r["model"]]["cost"] += r.get("cost_usd", 0.0)
    agg[r["model"]]["lat"].append(r.get("latency_s"))
for m, a in sorted(agg.items()):
    lat = (round(sum(a["lat"])/len(a["lat"]),1) if a["lat"] else "n/a")
    print(f"  {m:36s}  correct={a['ok']}/{a['n']}  total_cost=${a['cost']:.6f}  avg_latency={lat}s")
print("Saved: " + str(HERE / "raw.jsonl"))
PY