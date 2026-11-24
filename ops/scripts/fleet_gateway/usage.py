"""fleet_gateway.usage — the periodic model / token / cost notice for prompts.

WHY THIS EXISTS
    An agent cannot see its own spend. The gateway is the only party that sees
    every turn, so it is the right place to say "you are on model X, N tokens,
    $Y, Z% of the context window" — at most once per interval, never per turn.

THE RULE THAT MATTERS: REAL NUMBERS OR NO NOTICE
    A wrong number is worse than no number: an agent shown a fabricated cost
    will reason about it. So every source is read-only and every failure is
    silent — a source that cannot be read returns None and the prompt goes out
    exactly as it would have without this module.

TWO SOURCES, BOTH REAL
    * ``command_usage`` — a declared argv (e.g. ``pi-control stats``) that prints
      ``key=value`` pairs. This is the generic seam: a new harness ships its own
      reader, the gateway learns no new hardcoded backend.
    * ``hermes_usage`` — the agent's own ``state.db`` sessions row for this chat.
      The hermes backend talks to an agent over the bus; its token counts live in
      that agent's database, not in the gateway.

INTERVAL DISCIPLINE
    ``UsageNotice`` keeps one timer per session key. It marks the attempt BEFORE
    running the source, so a slow or broken source is retried at the interval,
    not on every turn. The first turn of a session is due immediately — the
    operator asked to SEE this, not to wait ten minutes for it.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("fleet_gateway.usage")

# ``key=value`` tokens separated by whitespace or a middle dot. A source prints
# one line; this tolerates the separators a human-readable formatter uses.
_TOKEN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s·]+)")

# The spellings a source may use. The first name is the canonical field.
_ALIASES = {
    "model": "model",
    "tokens": "tokens",
    "total_tokens": "tokens",
    "tok": "tokens",
    "cost": "cost_usd",
    "cost_usd": "cost_usd",
    "context": "context_percent",
    "context_percent": "context_percent",
    "percent": "context_percent",
    "context_tokens": "context_tokens",
    "context_window": "context_window",
}


@dataclass(frozen=True)
class UsageSnapshot:
    """One real measurement. Every field is optional — omit what was not read."""

    model: str = ""
    tokens: int | None = None
    cost_usd: float | None = None
    context_percent: float | None = None
    context_tokens: int | None = None
    context_window: int | None = None
    source: str = ""


def _int(value: str) -> int | None:
    try:
        return int(float(value.replace(",", "")))
    except (TypeError, ValueError):
        return None


def _float(value: str) -> float | None:
    try:
        return float(value.replace(",", "").rstrip("%$"))
    except (TypeError, ValueError):
        return None


def parse_usage(text: str) -> UsageSnapshot | None:
    """Parse a source's ``key=value`` line → UsageSnapshot, or None.

    Returns None when the text carries neither a model nor a token count: a
    partial line (``cost=0.0``) is not a measurement of anything.
    """
    if not text:
        return None
    found: dict[str, str] = {}
    for key, value in _TOKEN.findall(text):
        field = _ALIASES.get(key.lower())
        if field:
            found[field] = value
    if "model" not in found and "tokens" not in found:
        return None
    snap = UsageSnapshot(
        model=found.get("model", ""),
        tokens=_int(found["tokens"]) if "tokens" in found else None,
        cost_usd=_float(found["cost_usd"]) if "cost_usd" in found else None,
        context_percent=(_float(found["context_percent"])
                         if "context_percent" in found else None),
        context_tokens=(_int(found["context_tokens"])
                        if "context_tokens" in found else None),
        context_window=(_int(found["context_window"])
                        if "context_window" in found else None),
    )
    if snap.tokens is None and not snap.model:
        return None
    return snap


def _percent(value: float) -> str:
    """``19.872665`` → ``19.9%``; ``30.0`` → ``30%``."""
    rounded = round(float(value), 1)
    return f"{rounded:g}%"


def format_notice(snap: UsageSnapshot | None) -> str:
    """One prompt line, omitting what was not measured. '' when nothing is left.

    A ZERO token count or cost is omitted rather than printed: a provider that
    does not report usage returns 0, and "tokens=0" reads as a broken meter. The
    honest form is to print nothing for a field we cannot stand behind.
    """
    if snap is None:
        return ""
    parts: list[str] = []
    if snap.model:
        parts.append(f"model={snap.model}")
    if snap.tokens:
        parts.append(f"tokens={snap.tokens:,}")
    if snap.cost_usd:
        parts.append(f"cost=${snap.cost_usd:,.4f}")
    if snap.context_percent is not None:
        parts.append(f"context={_percent(snap.context_percent)}")
    elif snap.context_tokens is not None and snap.context_window:
        parts.append(f"context={_percent(100.0 * snap.context_tokens / snap.context_window)}")
    if not parts:
        return ""
    return "[gateway-usage] " + " · ".join(parts)


def command_usage(argv: list[str], timeout_s: float = 10.0) -> UsageSnapshot | None:
    """Run a declared reader and parse its stdout. Never raises, never guesses."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        log.debug("usage command failed to run: %s", exc)
        return None
    if proc.returncode != 0:
        log.debug("usage command exited %s: %s", proc.returncode,
                  (proc.stderr or "").strip()[:200])
        return None
    snap = parse_usage(proc.stdout or "")
    if snap is not None:
        snap = UsageSnapshot(**{**snap.__dict__, "source": "command"})
    return snap


def hermes_usage(db_path: str, chat_id: str) -> UsageSnapshot | None:
    """Read the agent's own session row for this chat. Read-only, never raises.

    The hermes backend does not see token counts — they live in the agent's
    ``state.db``. Missing database, missing table, missing row all mean "no
    measurement", which means no notice.
    """
    if not db_path or not chat_id:
        return None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error as exc:
        log.debug("usage db unavailable: %s", exc)
        return None
    try:
        row = con.execute(
            "SELECT model, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, estimated_cost_usd FROM sessions "
            "WHERE chat_id = ? ORDER BY started_at DESC LIMIT 1",
            (str(chat_id),),
        ).fetchone()
    except sqlite3.Error as exc:
        log.debug("usage query failed: %s", exc)
        return None
    finally:
        con.close()
    if not row:
        return None
    model, inp, out, cache_r, cache_w, cost = row
    tokens = sum(v for v in (inp, out, cache_r, cache_w) if isinstance(v, int))
    if not tokens and not cost:
        return None
    return UsageSnapshot(
        model=str(model or ""),
        tokens=tokens or None,
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        source="hermes",
    )


class UsageNotice:
    """Per-session interval gate around a source. Thread-safe.

    ``clock`` is injectable so the interval is testable without sleeping.
    """

    def __init__(self, interval_s: float = 600.0,
                 clock: Callable[[], float] = time.monotonic):
        # Fail at construction, not on the first prompt: a negative interval
        # would make every turn "due" and a non-numeric one would raise mid-turn.
        try:
            interval = float(interval_s)
        except (TypeError, ValueError):
            raise ValueError(f"interval_s must be a number, got {interval_s!r}") from None
        if interval < 0:
            raise ValueError(f"interval_s must be >= 0, got {interval_s!r}")
        if not callable(clock):
            raise ValueError("clock must be callable")
        self.interval_s = interval
        self._clock = clock
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def due(self, key: str) -> bool:
        with self._lock:
            last = self._last.get(key)
            if last is None:
                return True
            return (self._clock() - last) >= self.interval_s

    def notice(self, key: str, source) -> str:
        """The formatted notice when due and readable, else ''.

        The attempt is marked BEFORE the source runs: a source that hangs or
        fails must not be retried on every turn.
        """
        with self._lock:
            last = self._last.get(key)
            now = self._clock()
            if last is not None and (now - last) < self.interval_s:
                return ""
            self._last[key] = now
        try:
            snap = source()
        except Exception as exc:  # noqa: BLE001 — a source must never break a turn
            log.debug("usage source raised: %s", exc)
            return ""
        return format_notice(snap)
