#!/usr/bin/env python3
"""
MCP server for DeepSeek adversarial review via OpenRouter — FLEET-OWNED copy.

The fleet keeps its own deepseek review MCP server here so a host does not
depend on a product repo checkout (~/Developer/AI/steadfaste-s1-model) to run
adversarial reviews. Only the pi registration (the `deepseek` entry in
~/.pi/agent/mcp.json) may point at either copy; this file is what fleet-update
deploys. Do not fork it further — extend THIS file, rebuild on top of it.

Default model: deepseek/deepseek-v4-pro-0813 (canonical fleet reviewer; the
cost-controlled pin chosen over plain v4-pro — input $0.111/M vs ~$0.90/M on
OpenRouter, 2026-11-11). Plain deepseek/deepseek-v4-pro and
deepseek/deepseek-chat-v3 are NOT used anywhere here as defaults.

The OPENROUTER_API_KEY is resolved ONCE at startup — env var first, then a
~/.steadfaste-fleet/.env, then a repo-local .env — so the agent never handles
the secret in a session.

Fleet-owned copy landed by operator decision 2026-11-11 (Luke) — the change
reverses the earlier "never copy the server into steadfaste-fleet" rule; this
file is now source-of-record. See docs/external/mcp-deepseek-review.md.

Tools:
    review              — send a plan/memo to DeepSeek for adversarial review
    ask_deepseek        — send a generic research question to DeepSeek
    list_available_models — list DeepSeek models available on OpenRouter
"""

import asyncio
import importlib.util
import logging
import os
import re
import sys
import time
import traceback

# ── Dependency check ─────────────────────────────────────────
if importlib.util.find_spec("mcp") is None:
    print("ERROR: 'mcp' package not found. Install: pip install mcp", file=sys.stderr)
    sys.exit(1)

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[mcp-server] %(levelname)s: %(message)s",
    stream=sys.stderr,
    force=True,
)
log = logging.getLogger("deepseek-review-mcp")

# ── Imports that depend on mcp ──────────────────────────────
from mcp.server import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool  # noqa: E402

import requests  # noqa: E402

APP_NAME = "steadfaste-fleet-deepseek"
BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"

# Canonical fleet reviewer. 0813 is the cost-controlled pin; do not change this
# back to plain v4-pro or chat-v3 — see docs/external/mcp-deepseek-review.md.
# DRY (2026-11-13, cycle 8265): the default resolves through fleet_lib.models
# (the single source) so changing the reviewer is a one-line edit there, and
# a host env ADVERSARIAL_REVIEWER_MODEL is honored.
try:
    _SCRIPT_DIR_ = os.path.dirname(os.path.abspath(__file__))
    for _feel in (_SCRIPT_DIR_, os.path.dirname(_SCRIPT_DIR_)):
        if os.path.isdir(os.path.join(_feel, "fleet_lib")):
            sys.path.insert(0, _feel)
            break
    from fleet_lib.models import get_reviewer_model
    DEFAULT_MODEL = get_reviewer_model()
except Exception:  # noqa: BLE001 — fail-safe fallback if fleet_lib absent
    DEFAULT_MODEL = "deepseek/deepseek-v4-pro-0813"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ENV_FILE_PATHS = [
    os.path.expanduser("~/.steadfaste-fleet/.env"),
    os.path.join(_REPO_ROOT, ".env"),
]

# Matches: optional leading whitespace, optional `export ` prefix,
# OPENROUTER_API_KEY=..., and a value up to end-of-line (tolerates CRLF).
_ENV_KEY_RE = re.compile(
    r"^\s*(?:export\s+)?OPENROUTER_API_KEY\s*=\s*(.+?)\s*$", re.MULTILINE
)


def _read_key_from(path: str) -> str:
    """Parse OPENROUTER_API_KEY out of a .env file.

    Must tolerate real .env syntax — leading whitespace, `export `, surrounding
    quotes, CRLF line endings. Returns "" when absent or unreadable.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:  # noqa: BLE001 — any read failure means "no key here"
        return ""
    match = _ENV_KEY_RE.search(text)
    if not match:
        return ""
    return match.group(1).strip().strip('"').strip("'")


# Resolve the key ONCE at startup: env var first, then the shared secrets
# store (~/.steadfaste-fleet/.env), then a repo-local .env as a last resort.
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
if not API_KEY:
    for _candidate in _ENV_FILE_PATHS:
        if os.path.isfile(_candidate):
            API_KEY = _read_key_from(_candidate)
            if API_KEY:
                break

if not API_KEY or len(API_KEY) < 10:
    print("ERROR: OPENROUTER_API_KEY not set or too short.", file=sys.stderr)
    sys.exit(1)


def _err(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=f"Error: {text}")], isError=True)


def _call_deepseek(system_prompt: str, user_prompt: str, model: str = DEFAULT_MODEL,
                   max_tokens: int = 8000, temperature: float = 0.7) -> str:
    """Call OpenRouter DeepSeek. Returns the markdown review text.

    Raises RuntimeError on a transport/API failure so the caller surfaces it as
    an isError tool result — never as a fake clean review verdict. The key is
    never logged or echoed; any API error body is scrubbed of secret-looking
    tokens before it is returned.
    """
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "X-Title": "Steadfaste Fleet Review",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    start = time.time()
    try:
        resp = requests.post(BASE_URL, headers=headers, json=payload, timeout=300)
    except requests.RequestException as exc:
        raise RuntimeError(f"OpenRouter request failed: {exc}") from exc
    elapsed = time.time() - start

    if resp.status_code != 200:
        err = resp.text[:500]
        err = re.sub(r"sk-[a-zA-Z0-9_-]{8,}", "[REDACTED]", err)
        err = re.sub(r"Bearer\s+\S+", "Bearer [REDACTED]", err)
        raise RuntimeError(f"OpenRouter HTTP {resp.status_code}: {err}")

    data = resp.json()
    if "choices" not in data or not data["choices"]:
        raise RuntimeError(f"OpenRouter unexpected response: {list(data.keys())}")

    content = data["choices"][0]["message"]["content"]
    return f"<!-- DeepSeek response ({model}) in {elapsed:.1f}s -->\n\n{content}"


# ── Review prompts (fixed, committed here — not caller-authored) ──
_REVIEW_SYSTEM = (
    "You are a senior machine learning researcher conducting an adversarial review "
    "of an experimental plan and results. Be harsh but constructive. Find methodological gaps, "
    "unstated assumptions, confirmation bias, and design flaws. Cite specific chunks. "
    "If the plan is flawed, say so directly. Do not praise for the sake of politeness. "
    "Structure your review with: (1) Truth check / sanity check, (2) Major concerns, "
    "(3) Minor concerns, (4) Recommended fixes, (5) Meta-recommendation (what to do next)."
)
_ASK_SYSTEM = (
    "You are a helpful senior researcher. Answer concisely but thoroughly. "
    "When discussing ML/AI topics, cite relevant papers or well-known techniques where applicable."
)


def _tool_review(args: dict) -> CallToolResult:
    plan_text = args.get("plan_text", "")
    model = args.get("model", DEFAULT_MODEL)
    focus = args.get("focus_questions", "")
    context = args.get("context", "")
    if not plan_text.strip():
        return _err("review: plan_text is required")
    # NEUTRAL preamble. Context belongs in the memo itself, which the caller
    # writes; the server must not inject its own narrative.
    user_prompt = (f"{context}\n\n" if context else "") + f"{plan_text}\n\n"
    if focus:
        user_prompt += f"\nSpecific questions to focus on:\n{focus}\n"
    try:
        result = _call_deepseek(_REVIEW_SYSTEM, user_prompt, model=model)
        return CallToolResult(content=[TextContent(type="text", text=result)])
    except Exception as exc:  # noqa: BLE001 — MCP boundary
        log.error("review failed (%s): %s", model, exc)
        return _err(str(exc))


def _tool_ask(args: dict) -> CallToolResult:
    question = args.get("question", "")
    model = args.get("model", DEFAULT_MODEL)
    if not question.strip():
        return _err("ask_deepseek: question is required")
    try:
        result = _call_deepseek(_ASK_SYSTEM, question, model=model)
        return CallToolResult(content=[TextContent(type="text", text=result)])
    except Exception as exc:  # noqa: BLE001 — MCP boundary
        log.error("ask_deepseek failed (%s): %s", model, exc)
        return _err(str(exc))


def _tool_models(args: dict) -> CallToolResult:
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    try:
        resp = requests.get(MODELS_URL, headers=headers, timeout=30)
        data = resp.json()
        models = sorted(m["id"] for m in data.get("data", []) if "deepseek" in m.get("id", ""))
        text = "Available DeepSeek models on OpenRouter:\n" + "\n".join(f"  - {m}" for m in models)
        return CallToolResult(content=[TextContent(type="text", text=text)])
    except Exception as exc:  # noqa: BLE001 — MCP boundary
        log.error("list_available_models failed: %s", exc)
        return _err(f"Error listing models: {exc}")


_HANDLERS = {
    "review": _tool_review,
    "ask_deepseek": _tool_ask,
    "list_available_models": _tool_models,
}


async def list_tools(ctx, params=None) -> ListToolsResult:
    return ListToolsResult(tools=[
        Tool(
            name="review",
            description="Send an experimental plan or memo to DeepSeek for adversarial review. Returns a harsh but constructive critique with methodological gaps, confirmation bias, unstated assumptions, and recommended fixes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "plan_text": {
                        "type": "string",
                        "description": "The full text of the experimental plan, memo, or results summary to review.",
                    },
                    "model": {
                        "type": "string",
                        "default": DEFAULT_MODEL,
                        "description": f"DeepSeek model to use. Defaults to {DEFAULT_MODEL}.",
                    },
                    "focus_questions": {
                        "type": "string",
                        "default": "",
                        "description": "Optional specific questions to focus on the review.",
                    },
                },
                "required": ["plan_text"],
            },
        ),
        Tool(
            name="ask_deepseek",
            description="Send a generic research question to DeepSeek. Useful for quick literature checks, mathematical proofs, or brainstorming.",
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The research question or prompt."},
                    "model": {"type": "string", "default": DEFAULT_MODEL},
                },
                "required": ["question"],
            },
        ),
        Tool(
            name="list_available_models",
            description="List DeepSeek models available on OpenRouter.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ])


async def call_tool(ctx, params=None) -> CallToolResult:
    name = params.name if params else ""
    args = (params.arguments or {}) if params else {}
    try:
        handler = _HANDLERS.get(name)
        if not handler:
            return _err(f"Unknown tool: {name}")
        return handler(args)
    except Exception as exc:  # noqa: BLE001 — MCP boundary
        log.error("unexpected error in call_tool(%s): %s", name, exc, exc_info=True)
        return _err(str(exc))


# ── Server ───────────────────────────────────────────────────
server = Server(APP_NAME, on_list_tools=list_tools, on_call_tool=call_tool)


# ── Main ─────────────────────────────────────────────────────
async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:  # noqa: BLE001 — full traceback to stderr so MCP debug is possible
        traceback.print_exc(file=sys.stderr)