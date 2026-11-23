#!/usr/bin/env python3
"""
fleet_lib/models.py — Shared model configuration loader (canonical, Hermes-independent).

Canonical home of the model-config logic in hermes_models.py, moved so no fleet
component requires the Hermes runtime to resolve it (component-hermes-separation
S1c). `hermes_models` remains as a thin re-export shim.

All Ollama model names used across the project are configured in
~/steadfaste-fleet/.env (preferred) or ~/.hermes/models.env (legacy fallback),
which survive fleet-update.sh (they're outside the repo).
This module provides a single importable function that every script uses,
replacing scattered hardcoded constants with a unified lookup.

Resolution priority (highest to lowest):
  1. Runtime environment variable (os.environ) — for one-off overrides
  2. ~/steadfaste-fleet/.env — unified config file (new, preferred)
  3. ~/.hermes/models.env — legacy model-only config (fallback)
  4. Hardcoded default — shipped with the repo, always the last fallback

Defined env vars (see ~/steadfaste-fleet/.env for current values):
  JUDGE_MODEL       — LLM-as-judge scorer (default: qwen2.5:3b)
  EMBEDDING_MODEL   — Text embeddings (default: nomic-embed-text)
  CODING_MODEL      — Code generation via offline_code (default: auto-detected)
  CREATIVE_MODEL    — Creative/text generation (consumer: agent-daily-bible-reading.py,
                      which reads it with get_model() and guards it with qualify_model())

Usage:
    from fleet_lib.models import get_model
    from hermes_models import get_model  # back-compat shim

    judge = get_model("JUDGE_MODEL", "qwen2.5:3b")
    embed = get_model("EMBEDDING_MODEL", "nomic-embed-text:v1.5")
"""

import os
import sys

_HERMES_FLEET_ENV = os.path.expanduser("~/steadfaste-fleet/.env")
_LEGACY_MODELS_ENV = os.path.expanduser("~/.hermes/models.env")
_CACHE = None


def _load_env_file(path: str) -> dict[str, str]:
    """Load KEY=value pairs from a simple env file (no ``export`` keyword)."""
    result: dict[str, str] = {}
    if not os.path.isfile(path):
        return result
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                    result[key.strip()] = val.strip().strip("'\"").strip()
    except (OSError, IOError) as exc:
        # The file's ABSENCE is a supported state (handled above); a present-but-unreadable
        # file is a real problem that would otherwise show up as "every model silently
        # reverted to its default" — say so rather than returning an empty config.
        print(f"WARNING: cannot read {path}: {exc}", file=sys.stderr)
    return result


def load_models_env() -> dict[str, str]:
    """Load model env vars from ~/steadfaste-fleet/.env (preferred),
    falling back to ~/.hermes/models.env (legacy).

    Returns a dict of KEY=value pairs.  Results are cached after first read.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    # Try the new unified file first, then the legacy file
    result = _load_env_file(_HERMES_FLEET_ENV)
    if not result:
        result = _load_env_file(_LEGACY_MODELS_ENV)

    _CACHE = result
    return _CACHE


def get_model(env_var: str, default: str) -> str:
    """Resolve a model name from env var → env files → default.

    Args:
        env_var: Name of the environment variable (e.g. "JUDGE_MODEL").
        default: Hardcoded fallback shipped with the repo.

    Returns:
        The resolved model name string.
    """
    models_env = load_models_env()
    return models_env.get(env_var) or os.environ.get(env_var) or default


# ── Canonical adversarial reviewer (DRY, 2026-11-13, cycle 8265) ────────────
# ONE source of truth for the reviewer model. The Close-gate (loop-gov-mcp),
# plan reviewer (adversarial-review.py), deepseek-review-mcp and the pi agent
# backend all resolve their default here; changing the reviewer (e.g. to a
# cheaper model) is a ONE-LINE edit to the constant, not 5 files. The env var
# ADVERSARIAL_REVIEWER_MODEL is honored first everywhere.
# canonical reviewer — winner of docs/evidence/plan-reviewer-model-eval-2026-11-13/
# (equal recall, ~6x cheaper, fastest) and docs/evidence/codegen-pro-models-2026-11-13/
# (6/6 at the lowest measured cost). Change it via ADVERSARIAL_REVIEWER_MODEL.
REVIEWER_MODEL_DEFAULT = "xiaomi/mimo-v2.6-pro"


def get_reviewer_model() -> str:
    """Resolve the reviewer default: env ADVERSARIAL_REVIEWER_MODEL, else canonical.

    Reads process env FIRST, then the env file, then the canonical constant —
    matching the reviewer surfaces' own `_env_value` semantics (a host's
    process-level override must win, exactly as loop-gov reads it), and
    differing from the generic get_model() order on purpose.
    """
    filename = os.environ.get("ADVERSARIAL_REVIEWER_MODEL") \
        or load_models_env().get("ADVERSARIAL_REVIEWER_MODEL")
    return filename or REVIEWER_MODEL_DEFAULT


def qualify_model(value: str, default: str, *, endpoint: str = "") -> str:
    """Guard a model id that an HTTP endpoint addresses as ``provider/model``.

    CREATIVE_MODEL is the fleet's *creative* text path and its consumer reaches a remote
    OpenAI-compatible endpoint (agent-daily-bible-reading.py). Its value lives in a
    per-host ``~/steadfaste-fleet/.env``, so a host may carry a *bare* Ollama-style id
    (``deepseek-v4-flash``) that the endpoint cannot resolve. Rather than send an
    unusable id upstream and fail the run, fall back to ``default`` and say so on
    stderr — a silent substitution would hide a real config mistake.

    Callers read the contract with ``get_model()`` (the form the fleet's consumer scan
    recognises) and hand the resolved value here.

    Args:
        value: The resolved model id.
        default: Provider-qualified fallback (e.g. "deepseek/deepseek-chat").
        endpoint: Endpoint the id will be sent to (for the warning only).

    Returns:
        A provider-qualified model id.
    """
    if "/" in value:
        return value
    print(
        f"WARNING: {value!r} is not a provider-qualified model id "
        f"(expected 'provider/model'); using {default!r}"
        + (f" against {endpoint}" if endpoint else ""),
        file=sys.stderr,
    )
    return default


def _clear_cache() -> None:
    """Clear the cached steadfaste-fleet.env contents (for testing)."""
    global _CACHE
    _CACHE = None


# ── Fleet-owned session/cron model resolvers (decouple-fleet-model-materializer,
#    cycle 8566) ──────────────────────────────────────────────────────────────
# steadfaste-fleet is the SINGLE authority for the session/cron model; Hermes
# config.yaml is a MATERIALIZED artifact, not the source. The derive-and-decide
# logic lives ONLY here in Python (reviewer ADV-0-2) so it is directly testable;
# the shell materializer (materialize-fleet-model.sh) is a thin caller.
#
# Source priority (highest -> lowest):
#   1. process env / env-dict (SESSION_MODEL / SESSION_PROVIDER)   — one-off override
#   2. ~/steadfaste-fleet/.env                                      — per-host override
#   3. config/router-models.json `baseline`                        — fleet canonical
# The baseline is a routing-labelled `<provider>:<provider>/<model>` id; the
# resolver STRIPS the label prefix to the bare `provider/model` that hermes
# config.yaml must hold (a label 400s every turn).

_FLEET_ROUTER_MODELS = os.path.expanduser("~/steadfaste-fleet/config/router-models.json")


def _router_models_baseline(router_models_path: str = "") -> str:
    """Return the `baseline` field of the fleet router-models.json, or '' if absent."""
    path = router_models_path or _FLEET_ROUTER_MODELS
    try:
        with open(path) as f:
            import json
            doc = json.load(f)
            return str(doc.get("baseline") or "")
    except (OSError, ValueError):
        return ""


def _strip_routing_label(value: str) -> tuple[str, str, bool]:
    """Split a routing-labelled `<label>:<org>/<model>` id into (model, provider, had_label).

    A routing label is `label:org/model` — a colon AND a slash AFTER it
    (hermes-model-config: an Ollama tag like `qwen2.5:3b` has a colon but no slash
    after it, and is NOT a routing label). Returns the bare `org/model` id as
    `model`, the `label` as `provider` (the fleet convention: the label names the
    provider/key), and whether a label was present.
    """
    if ":" in value and "/" in value.split(":", 1)[1]:
        label, _, model = value.partition(":")
        return model, label, True
    return value, "", False


def resolve_session_target(source_env: dict[str, str] | None = None,
                           router_models: dict | None = None) -> dict:
    """Resolve the canonical session model target (model, provider) from fleet sources.

    Pure derive step — the idempotence decision and validation live here so they are
    testable independent of Hermes. Returns a dict with keys:
      model, provider, has_label, source
    where `source` is one of "env", "envfile", "baseline".
    """
    src_env = dict(source_env or os.environ)
    model = src_env.get("SESSION_MODEL", "") or load_models_env().get("SESSION_MODEL", "")
    provider = src_env.get("SESSION_PROVIDER", "") or load_models_env().get("SESSION_PROVIDER", "")
    if model:
        return {"model": model, "provider": provider,
                "has_label": _is_label_pattern(model), "source": "env"}
    # fall back to the router-models.json baseline (fleet canonical), label-stripped
    baseline = ""
    if router_models and isinstance(router_models, dict):
        baseline = str(router_models.get("baseline") or "")
    if not baseline:
        baseline = _router_models_baseline()
    bare, implied_provider, _ = _strip_routing_label(baseline)
    return {"model": bare, "provider": provider or implied_provider,
            "has_label": _is_label_pattern(bare), "source": "baseline"}


def _is_label_pattern(value: str) -> bool:
    """True when `value` still carries a routing label (`label:org/model`).

    The fleet baseline is legitimately routing-labelled (`openrouter:org/model`);
    after `_strip_routing_label` the bare id must be clean. This check catches a
    DOUBLE label (openrouter:nous:deepseek/x) or a leaked label that would 400
    every turn if written into model.default. An Ollama tag (`qwen2.5:3b`) has a
    colon but no slash after it, and is not a routing label.
    """
    return ":" in value and "/" in value.split(":", 1)[1]


def session_change_decision(current_model: str, target: dict) -> bool:
    """Idempotence check: True only when the materializer must write model.default.

    A routing label on the target is a RED state that must never be written (a label
    in model.default 400s every turn) — but the decision here is about WRITE-versus-noop,
    so a labelled target counts as "change" (the caller then fails closed rather than
    writing). The bare-id VALIDATION is the caller's job (fail loudly, do not write).
    """
    return current_model != target["model"] or target["has_label"] is True


def get_session_model() -> dict:
    """Return the resolved session model target as a dict (callers read .model/.provider)."""
    return resolve_session_target()


def get_cron_model() -> str:
    """Resolve the fleet cron model id (primary): SESSION or CRON env -> baseline."""
    env = dict(os.environ)
    for k in ("CRON_MODEL", "SESSION_MODEL"):
        v = env.get(k, "") or load_models_env().get(k, "")
        if v:
            return v
    return get_session_model()["model"]
