"""write_class.py — the host-neutral WRITE-CLASSIFICATION contract.

One implementation of "is this tool call a write?" for EVERY coding agent.
Extracted verbatim from the Hermes enforcer
(`plugins/governance-enforcer/__init__.py`), so a Hermes session and a pi/codex/
claude session classify a command identically — the "as well as the hermes
gateway" parity promise.

This module imports NOTHING harness-specific (stdlib only), so it can be
imported by the enforcer plugin, the loop-gov CLI, the loop-gov MCP server, and
any coding-agent adapter — one implementation, per-host ADAPTERS
(`docs/design/agent-interop.md`).

The extraction is proven lossless by `tests/test_write_class_parity.py`, which
asserts this module's verdicts equal a golden corpus captured from the current
enforcer.

Security invariants (carried over from the enforcer, still binding):
  * Interpreter `-c` forms (python3 -c, bash -c, node -e) are NEVER read-only —
    "no write intent" is not observable from the command string.
  * `doctor --fix` is not read-only (mutates the fleet).
  * sqlite3 CLI is not allowlisted (`.shell`, `-cmd`, load_extension execute).
  * Fail closed: a command is lock-free ONLY when it matches the strict
    read-only allowlist AND carries no composing metacharacter.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict

# ── Tool-name write classification ──────────────────────────────────────────

WRITE_TOOLS = {
    "write_file",
    "patch",
    "execute_code",
    "memory",
    "text_to_speech",
}

READ_TOOLS = {
    "read_file",
    "search_files",
    "session_search",
    "web_search",
    "web_extract",
    "vision_analyze",
    "skill_view",
    "skills_list",
    "tool_search",
    "tool_describe",
}

CONDITIONAL_WRITE_TOOLS = {
    "terminal",
    "cronjob",
    "skill_manage",
    "process",
    "computer_use",
}

WRITE_CRON_ACTIONS = {"create", "update", "remove"}
WRITE_SKILL_ACTIONS = {"create", "edit", "delete", "write_file", "remove_file", "patch"}
WRITE_PROCESS_ACTIONS = {"write", "submit", "close", "kill"}
WRITE_COMPUTER_USE_ACTIONS = {
    "click", "double_click", "right_click", "middle_click",
    "drag", "scroll", "type", "key", "set_value", "focus_app",
}

# Terminal commands that modify state — require governance lock.
WRITE_COMMAND_PATTERNS = [
    r"\s*(sudo\s+)?(rm|mv|cp|install|apt|apt-get|dpkg|brew|make|cmake|docker compose|kubectl)\s",
    r"\s*(sudo\s+)?(systemctl|service)\s+(start|stop|restart|reload|enable|disable|daemon-reload)\s",
    r"\s*(sudo\s+)?(chmod|chown|chattr|mkfs|fdisk|mount|umount|dd)\s",
    r"\s*(sudo\s+)?(sed|awk|tee)\s.*-i\s",
    r"\s*(sudo\s+)?(git)\s+(push|commit|merge|rebase|reset|cherry-pick|branch\s+-[dD]|tag|stash|checkout|restore|clean|rm|mv|update-ref|config|submodule)\s",
    r"\s*(sudo\s+)?(cronjob)\s+(create|update|remove|delete)",
    r"\s*(sudo\s+)?(uv|python3?)\s.*-(m\s+pip\s+install)",
    # guard: (python|python3)\s.*-c
    r"\s*(sudo\s+)?python3?\s+-c\s",
    # guard: (bash|sh|zsh)\s+-c
    r"\s*(sudo\s+)?bash\s+-c\s",
    r"\s*(sudo\s+)?wget\s.*-O\s",
    r"\s*(sudo\s+)?curl\s.*-o\s",
    r"\s*(sudo\s+)?nohup\s",
    r"\s*(sudo\s+)?docker\s+(run|build|push|commit|tag|save|load|rmi|system\s+prune)",
    r"\s*(sudo\s+)?crontab\s",
    r"\s*(sudo\s+)?(usermod|groupmod|useradd|groupadd|passwd)\s",
    r"\s*(sudo\s+)?ufw\s+(enable|disable|allow|deny|reject|delete|reset)",
    r"\s*(sudo\s+)?nginx\s+(-s\s+(reload|stop|quit))",
    r"\s*(sudo\s+)?journalctl\s+--rotate",
    r"\s*(sudo\s+)?(printf|cat|tee|head|tail|grep|find)\s+.*>\s",
    r"\s*(sudo\s+)?(printf|cat)\s+.*<<\s",
    r"\s*(sudo\s+)?(touch|mkdir|ln|rsync|unzip|tar|mkfifo|tee)\s",
    r"\s*(sudo\s+)?(npx|yarn|go|cargo|flatpak|snap)\s",
    r"\s*(sudo\s+)?(pip3?|npm)\s+(install|uninstall|remove|update|upgrade)",
    r"\s*echo\s+.*>\s",
    # guard: interpreter + script file execution
    r"\s*(sudo\s+)?(python3(?:\.\d+)?|node|ruby|perl|bash|sh|zsh)\s+[^\s-][^\s]*\.(py|js|rb|pl|sh)(?:\s|$)",
]


def is_terminal_write(args: Dict[str, Any]) -> bool:
    """True when a terminal tool call's command string is a write/modify op."""
    command = args.get("command", "")
    if not command:
        return False
    for pattern in WRITE_COMMAND_PATTERNS:
        if re.search(pattern, command):
            return True
    return False


# Read-only terminal commands that NEVER need a governance lock.
READONLY_COMMAND_PATTERNS = [
    r"^\s*(ls|cat|head|tail|less|more|grep|find|which|whoami|id|pwd|date|stat|file|du|wc|sort|uniq|diff|comm|env|printenv|getent|nproc|lscpu|lsblk|pgrep)(?:\s|$)",
    r"^\s*(ps|top|htop|df|free|uptime|uname|hostname|dmesg|journalctl|ss|netstat)(?:\s|$)",
    r"^\s*(cd|echo|printf|true|false)(?:\s|$)",
    r"^\s*(?:test|\[)(?:\s|$)",
    r"^\s*command\s+-[vV](?:\s|$)",
    r"^\s*(git)\s+(?:-C\s+\S+\s+|--no-pager\s+|--no-optional-locks\s+)*(status|log|diff|show|branch|stash\s+list|rev-parse|rev-list|for-each-ref|ls-files|ls-tree|cat-file|show-ref|merge-base|describe)",
    r"^\s*(docker)\s+(ps|images|logs|inspect|stats|top|port|events|version|info|diff|history)",
    r"^\s*(docker\s+compose)\s+(ps|logs|config|top|images|events|port)",
    r"^\s*(pip|npm)\s+(list|show|search)",
    r"^\s*(hermes)\s+(--version|doctor|config\s+get|config\s+show|config\s+path|config\s+check|env-path)",
    r"^\s*(systemctl)\s+(is-active|is-enabled|is-failed|status|show|list-units|list-timers|list-sockets|list-services|list-jobs|list-dependencies|get-default|show-environment|cat)",
    r"^\s*(systemctl)\s+list-unit-files\b",
    r"^\s*curl\s+(-s\s+)?(-o\s+/dev/null\s+)?-s?I\s+",
    r"^\s*curl\s+(-s\s+)?-s\s+(https?://|file:///dev/|localhost:|127\.0\.0\.1:)",
    r"^\s*(hermes)\s+mcp\s+(list|test)\b",
    r"^\s*(mycortex)\s+(list|search|stats|doctor)\b",
    r"^\s*(mycortex)\s+sources\s+list\b",
    r"^\s*(mycortex)\s+answer\s+(search|stats)\b",
]

_COMMAND_FORBIDDEN_METACHARS = re.compile(r"[<>`]|\$\(|\|\||\n|(?<!&)&(?!&)")
_COMMAND_SEPARATORS = re.compile(r"&&|;|\|")

_READONLY_MUTATION_GUARDS = [
    re.compile(r"\bfind\b[^|;]*\s-(exec|execdir|ok|okdir|delete|fprint|fprintf|fls)\b"),
    re.compile(r"\bsort\b[^|;]*(\s-o\s|\s--output[=\s])"),
    re.compile(r"\bdate\b[^|;]*\s(-s|--set)\b"),
    re.compile(r"\bhostname\s+[^-\s]"),
    re.compile(r"\bdmesg\b[^|;]*\s(-C|-c|--clear)\b"),
    re.compile(r"\bjournalctl\b[^|;]*--(vacuum-\w+|rotate|flush|sync|relinquish-var)"),
    re.compile(
        r"\bcurl\b[^|;]*\s(-d|--data[\w-]*|-X|--request|-F|--form|-T|--upload-file"
        r"|-o|--output|-O|-J|--remote-name|-c|--cookie-jar)\b"
    ),
    re.compile(r"\bgit\b[^|;]*\s(--output|-o\s)"),
    re.compile(r"\b(cat|head|tail|grep|wc|diff|comm|uniq|stat)\b[^|;]*\s--output[=\s]"),
    re.compile(r"\bgit\s+branch\b[^|;]*(?:\s+(?!-)\S)"),
    re.compile(r"\benv\b[^|;&]*(?:\s+(?:-{1,2}[\w-]+|\w+=\S*))*\s+(?![- \w]+=)(?!-)[^|;&\s]"),
]


def is_readonly_command(command: str) -> bool:
    """Fail-closed read-only terminal check (compound-aware).

    Lock-free ONLY when: no forbidden metacharacter (>, <, `, $(), ||, lone
    &, newline); every `|`/`&&`/`;` segment independently matches the strict
    read-only allowlist; and no mutating flag rides inside an allowlisted
    primitive.
    """
    if not command:
        return False
    if re.search(r"hermes\s+doctor", command) and re.search(r"--fix|-f\b", command):
        return False
    if _COMMAND_FORBIDDEN_METACHARS.search(command):
        return False
    if "\n" in command:
        return False
    for guard in _READONLY_MUTATION_GUARDS:
        if guard.search(command):
            return False
    segments = [s.strip() for s in _COMMAND_SEPARATORS.split(command)]
    if not segments or any(not s for s in segments):
        return False
    for segment in segments:
        if not any(re.search(pattern, segment) for pattern in READONLY_COMMAND_PATTERNS):
            return False
    return True


# ── Sanctioned lock-free recovery command ────────────────────────────────────
# (2026-11-12: the pre-migration repo name `hermes-cortex` was REMOVED from the
# accept list — the migration is complete.)
_SANCTIONED_CORTEX_UPDATE_RE = re.compile(
    r"^\s*(?:bash\s+)?(?:~|" + re.escape(str(Path.home())) + r")/steadfaste-fleet/ops/scripts/(?:fleet-update|fleet-update)\.sh"
    r"(?:\s+(?:--dry-run|--status|--delta|--clean-stale))*\s*$"
)


def is_sanctioned_fleet_update_command(command: str) -> bool:
    if not command:
        return False
    if _COMMAND_FORBIDDEN_METACHARS.search(command) or _COMMAND_SEPARATORS.search(command):
        return False
    return _SANCTIONED_CORTEX_UPDATE_RE.fullmatch(command) is not None


# ── /tmp cleanup is sanctioned and lock-free ─────────────────────────────────
_TMP_SCRATCH_ROOTS = ("/tmp",)


def _resolve_scratch_roots() -> list:
    roots = []
    for r in _TMP_SCRATCH_ROOTS:
        try:
            roots.append(os.path.realpath(r))
        except (OSError, RuntimeError):
            continue
    return roots


def _operand_under_scratch(operand: str) -> bool:
    if not operand or not operand.startswith(("/", "$")):
        return False
    if any(ch in operand for ch in ("*", "?", "[", "{", "}", "~")):
        return False
    if operand.startswith("$"):
        operand = os.path.expandvars(operand)
        if not operand or not operand.startswith("/"):
            return False
    if "/.." in operand or operand.startswith("..") or operand.endswith("/.."):
        return False
    try:
        resolved = os.path.realpath(operand)
    except (OSError, RuntimeError):
        return False
    if resolved in _resolve_scratch_roots():
        return False
    for root in _resolve_scratch_roots():
        if resolved.startswith(root + "/"):
            return True
    return False


def is_sanctioned_tmp_cleanup_command(command: str) -> bool:
    if not command:
        return False
    if re.search(r"[<>`\[\]{}]|\$\(|\$\{|\|(?!\|)|(?:^|[^&]);(?:[^&]|$)|\n"
                 r"|\bsudo\b|\bmv\b|\bcd\b|\*\??|\?\??", command):
        return False
    if re.search(r"&&\s*[|;]|&&\s*$|^\s*&&", command):
        return False
    segments = _COMMAND_SEPARATORS.split(command)
    segments = [s.strip() for s in segments if s.strip()]
    if not segments:
        return False
    for i, seg in enumerate(segments):
        toks = seg.split()
        head = toks[0] if toks else ""
        if head in ("echo", "printf"):
            if i != len(segments) - 1:
                return False
            if len(toks) != 2 or toks[1].startswith(("-", "/", "$", "~")):
                return False
            continue
        if head not in ("rm", "rmdir", "/bin/rm", "/usr/bin/rm"):
            return False
        for tok in toks[1:]:
            if tok.startswith("-") and tok in ("-f", "-r", "-rf", "-fr", "-d"):
                continue
            if not _operand_under_scratch(tok):
                return False
    return True


# ── Plan-review record is sanctioned and lock-free ───────────────────────────
_PLAN_REVIEW_SCRIPT_NAMES = ("adversarial-review.py",)


def _plan_review_material_ok(command: str) -> bool:
    return bool(re.search(r"--material-file\s+\S+", command)) or bool(
        re.search(r"--material\s+\S+", command))


def is_sanctioned_plan_review_command(command: str) -> bool:
    if not command:
        return False
    if _COMMAND_FORBIDDEN_METACHARS.search(command) or _COMMAND_SEPARATORS.search(command):
        return False
    if re.search(r"\bsudo\b", command):
        return False
    if re.search(r"\b--sweep\b", command):
        return False
    if not re.search(r"\bpython3?\b", command):
        return False
    if not any(name in command for name in _PLAN_REVIEW_SCRIPT_NAMES):
        return False
    if not re.search(r"--cycle-id\s+\d+", command):
        return False
    if not re.search(r"--reviewer-id\s+\S+", command):
        return False
    if not _plan_review_material_ok(command):
        return False
    if not re.search(r"(?<![\w-])--re-review\b", command) and not re.search(
            r"(?<![\w-])--review\b", command):
        return False
    return True


# ── Loop-governance DB is append-only: raw mutation BLOCKED ──────────────────
_GOV_DB_BASENAME = "loop-governance.db"
_GOV_DB_MUTATE_VERBS = re.compile(
    r"(?:DELETE\s+FROM|DROP\s+TABLE|DROP\s+INDEX|UPDATE\s+\w+|"
    r"ALTER\s+TABLE|CREATE\s+TABLE|INSERT\s+INTO|REPLACE\s+INTO|"
    r"VACUUM|REINDEX)", re.IGNORECASE)
_RECORD_REVIEW_NAMES = ("record-review.py",)


def _mentions_governance_db(command: str) -> bool:
    return _GOV_DB_BASENAME in command


def _has_db_path_arg(command: str) -> bool:
    return bool(re.search(r"--db(?:\s+|=)\S*", command))


def _is_sanctioned_gov_db_writer(command: str) -> bool:
    if re.search(r"\bpython3?\b", command) and re.search(
            r"adversarial-review\.py", command):
        return True
    if re.search(r"\bpython3?\b", command) and any(
            n in command for n in _RECORD_REVIEW_NAMES):
        return True
    return False


def is_governance_db_mutation_command(command: str) -> bool:
    """True when a write-class command mutates the governance DB outside the
    sanctioned writers. Fail open for reads — targets MUTATION only."""
    if not command or not _mentions_governance_db(command):
        return False
    if _is_sanctioned_gov_db_writer(command):
        return False
    if re.search(r"\bsqlite3\b", command) and _GOV_DB_MUTATE_VERBS.search(command):
        return True
    if re.search(r"\bpython3?\b", command) and _GOV_DB_MUTATE_VERBS.search(command):
        return True
    if re.search(r"\bpython3?\b", command) and _has_db_path_arg(command):
        return True
    return False


# ── Tool-name dispatch ───────────────────────────────────────────────────────

def is_cronjob_write(args: Dict[str, Any]) -> bool:
    return args.get("action", "") in WRITE_CRON_ACTIONS


def is_skill_write(args: Dict[str, Any]) -> bool:
    return args.get("action", "") in WRITE_SKILL_ACTIONS


def is_process_write(args: Dict[str, Any]) -> bool:
    return args.get("action", "") in WRITE_PROCESS_ACTIONS


def is_computer_use_write(args: Dict[str, Any]) -> bool:
    return args.get("action", "") in WRITE_COMPUTER_USE_ACTIONS


def is_write_tool(tool_name: str, args: Dict[str, Any]) -> bool:
    if tool_name in WRITE_TOOLS:
        return True
    if tool_name == "terminal":
        command = args.get("command", "")
        return not is_readonly_command(command)
    if tool_name == "cronjob" and is_cronjob_write(args):
        return True
    if tool_name == "skill_manage" and is_skill_write(args):
        return True
    if tool_name == "process" and is_process_write(args):
        return True
    if tool_name == "computer_use" and is_computer_use_write(args):
        return True
    return False