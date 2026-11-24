#!/usr/bin/env python3
"""Refuse to start a gateway that would poll the LIVE bot's token.

Both gateway units read the SAME canonical env (`~/steadfaste-fleet/.env`). What
separates them is only the ``token_ref`` in their config:

    gateway.yaml                token_ref: TELEGRAM_BOT_TOKEN                  <- the PRIMARY bot
    gateway-esther0001.yaml     token_ref: TELEGRAM_BOT_TOKEN_ESTHER0001        <- a SECOND bot

The family is documented in docs/env-vars.md: TELEGRAM_BOT_TOKEN is the primary, and
each additional bot is TELEGRAM_BOT_TOKEN_<NAME>. Never name the primary here — a
gateway unit exists to run an ADDITIONAL bot.

``TELEGRAM_BOT_TOKEN`` is byte-identical to ``~/.hermes/.env`` — the bot
``hermes-gateway.service`` is already polling. A second poller on one bot token
gets a Telegram **409 Conflict**, and the casualty is whichever poller Telegram
drops: the LIVE channel.

That separation used to live only in a comment at the top of the unit file. **A
comment is not a guard — it cannot fail, and it never runs.** This runs as
``ExecStartPre``, so systemd REFUSES to start the unit instead of breaking the
live channel.

Usage::

    guard_distinct_token.py <config.yaml>

Exit codes: 0 = distinct (safe to start), 1 = same token as the live bot (refuse).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

LIVE_TOKEN_REF = "TELEGRAM_BOT_TOKEN"


def _fingerprint(secret: str) -> str:
    """Short hash — enough to compare and to name in a message, never the value."""
    return hashlib.sha256(secret.encode()).hexdigest()[:12]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("guard: usage: guard_distinct_token.py <config.yaml>", file=sys.stderr)
        return 1

    config_path = Path(argv[1])
    try:
        data = json.loads(config_path.read_text())
    except (OSError, ValueError) as e:
        print(f"guard: cannot read config {config_path}: {e}", file=sys.stderr)
        return 1

    bots = data.get("bots") or []
    if not bots:
        print(f"guard: {config_path} configures no bots", file=sys.stderr)
        return 1

    live = os.environ.get(LIVE_TOKEN_REF, "")
    live_fp = _fingerprint(live) if live else "(unset)"

    # Check EVERY configured bot, not just bots[0]. A config with several bots could
    # carry the primary token in its SECOND entry and slip through a bots[0]-only check
    # — which is exactly the shape the multi-token naming family encourages.
    seen: dict[str, str] = {}          # fingerprint -> token_ref (self-collision)
    for idx, bot in enumerate(bots):
        token_ref = str((bot or {}).get("token_ref") or "")
        if not token_ref:
            print(f"guard: {config_path} bots[{idx}] has no token_ref", file=sys.stderr)
            return 1

        # Naming the primary var IS the production config, whatever it currently holds.
        # Refuse on the NAME as well as the value: a rotated live token must not turn a
        # dangerous config into an accepted one.
        if token_ref == LIVE_TOKEN_REF:
            print(
                f"guard: REFUSING TO START — bots[{idx}].token_ref is {LIVE_TOKEN_REF}, "
                "the PRIMARY bot that hermes-gateway.service already polls.\n"
                "  This unit exists to run an ADDITIONAL bot. Point it at a distinct "
                "token from the family TELEGRAM_BOT_TOKEN_<NAME> "
                "(see gateway-esther0001.yaml, docs/env-vars.md).",
                file=sys.stderr)
            return 1

        mine = os.environ.get(token_ref, "")
        if not mine:
            print(f"guard: {token_ref} is not set in the environment — the gateway "
                  "cannot start without its token", file=sys.stderr)
            return 1

        if live and mine == live:
            print(
                "guard: REFUSING TO START — this config resolves to the PRIMARY bot's "
                "token.\n"
                f"  bots[{idx}].token_ref  : {token_ref}\n"
                f"  token fingerprint     : {_fingerprint(mine)}\n"
                f"  {LIVE_TOKEN_REF}       : {live_fp}  "
                "(the bot hermes-gateway.service polls)\n"
                "  A second poller on one bot token gets a Telegram 409 and the live "
                "channel is the casualty. Point this at a DISTINCT token.",
                file=sys.stderr)
            return 1

        # Two bots in ONE config sharing a token collide with each other, not with the
        # primary — the same 409, and nothing above would catch it.
        fp = _fingerprint(mine)
        if fp in seen:
            print(
                "guard: REFUSING TO START — bots in this config share a token.\n"
                f"  bots[{idx}].token_ref {token_ref} and {seen[fp]} both resolve to "
                f"fingerprint {fp}\n"
                "  One poller per token: these two would 409 each other.",
                file=sys.stderr)
            return 1
        seen[fp] = token_ref

    refs = ", ".join(str((b or {}).get("token_ref")) for b in bots)
    print(f"guard: ok — {refs} ({', '.join(sorted(seen))}) distinct from {LIVE_TOKEN_REF}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
