# Fleet-Gateway / Telegram Parity for a Coding-Agent Harness

How to bring a coding agent on a given harness (pi, claude-code, codex, whatever
appears next) to feature parity with the Hermes gateway/telegram. The
fleet-gateway is the host-neutral replacement for Hermes's in-process Telegram
adapter; the daemon/transport parity is already closed — do NOT rebuild it. The
per-harness work is wiring the agent in as a backend and reporting it.

## The core model

- `ops/scripts/fleet_gateway/` is the host-neutral Telegram daemon. Its parity
  matrix (`docs/design/fleet-gateway-parity.md`) is the reference for the
  transport/daemon surface (media, buttons, slash commands, streaming, typing,
  pairing) and is closed. Hermes's own adapter stays reachable as
  `hermes_backend.py` — this is additive, never a removal.
- Two backend kinds ship: `hermes` (bus adapter, `inbox_<a>`/`out_<a>`) and
  `command` (spawn any CLI per dispatch). The `command` kind — `CommandBackend`
  in `ops/scripts/fleet_gateway/agents.py`, NOT `backend.py` (which holds the
  `BackendAdapter` ABC) — is the generic door: pi / codex / claude / a bespoke
  script all run through it via a config row and nothing else.
- So "make <agent> on <harness> have parity with the Hermes gateway/telegram"
  means: the agent is reachable as a `command` (or `hermes`-bus) backend of the
  fleet-gateway, and that wiring is REGISTERED + DOCTOR-REPORTED.

## The per-harness work — five pieces shipped together

1. **Registry capability.** `ops/install/harnesses/registry.yaml` declares a
   `gateway` capability for the harness: `adapter: command`, `register:` the
   installer, `from: ops/scripts/fleet_gateway/agents.py` (the file that owns
   the kind registration), a `requires:` (the CLI + the bus reply path), a
   `why:` citing the parity doc, and `verify:` items that EXERCISE the seam
   (the gateway.yaml agents entry, the doctor row, the installer running).
   - Indent: `capabilities:` at 6 spaces, capability key at 6, its children at
     8, `verify:` list items at 10. The registry is YAML-validated on every
     write; a bad indent refuses the whole patch.
   - Moving a harness `documented → shipped` is only justified when EVERY
     `verify:` step is exercisable end-to-end — flipping status without a
     verifier is a lie (the "registering is not executing" rule, rule 9).
2. **Installer.** Mirror the working installer for the same server set. It must
   register the bus reply path MCP (`agent-bus`) PLUS the context server, write
   the harness's own config file idempotently (back up first, preserve foreign
   servers), resolve a Python that can `import mcp` (never a PATH default),
   offer `--check`/`--remove`, and end with a PASS/WARN verify line. A new
   installer must be added to `fleet-update.sh`'s register map or it never
   ships to hosts.
3. **Worked gateway config.** Ship a `gateway-titus-<harness>.example.yaml`
   mirroring the proven pi example: `kind: command`, ABSOLUTE binary path
   (argv, not a shell line — nothing expands `~`), `output: raw`, `session:
   per_chat`, `model_args` declared SEPARATELY from `command` (both at once is
   refused — a second `--model` makes the switch a silent no-op), and
   `commands.compact` (a CLI agent's /compact lives in its RPC mode, never as
   a forwarded prompt). Bot routing `default: <agent>`.
4. **Doctor row.** A harness that is present but unwired must be REPORTED, never
   silent (rule 9). The check reads: presence → bus reply MCP registered →
   gateway.yaml carries the agent spec. Emit PASS / WARN (naming the missing
   half) / DEFERRED (harness absent — not a fault).
5. **Parity tests — SPLIT, never conflate.** An echo-backed test through the
   daemon proves the DAEMON/SERIALIZER surface, never the HARNESS. Write two
   files:
   - daemon parity: drive the real fleet-gateway with a `command` backend at
     `/bin/echo` so the reply body IS the argv; assert the closed features.
     Host-neutral, no harness involved.
   - harness parity: drive the REAL binary (`pi --version` / `claude --version`
     exit 0 and print sane output), that gateway.yaml loads the harness's
     `command:` spec without raising (AgentSpec.from_dict + validate), and that
     the harness's MCP servers (per the registry verify list) are registered
     (`pi/claude mcp list`). One e2e bus round-trip for the host that has a live
     bot; skip-e2e is honest for one that does not.
   - Naming a test "per-harness parity" while it only exercises an echo backend
     is an over-claim a plan reviewer will flag.

## Pitfalls

- **Cite `agents.py` (CommandBackend) and `backend.py` (BackendAdapter ABC) as
  DIFFERENT files.** Mixing them is a fabrication finding. `CommandBackend` is
  the `kind=command` implementation; `backend.py` holds the seam interface. The
  registry `from:` field for a `command` capability points at `agents.py`.
- **`generate-harnesses.py` needs PyYAML** — run it with the fleet venv python
  (`~/.steadfaste-fleet/venv/bin/python3`), and run `--check` after regenerating
  so the derived READMEs + INDEX are current before committing. `registry.yaml`
  is the source of truth; derived files are NOT hand-edited.
- **A plan for this class comes back from pro plan-review with FINDINGS** (a
  plan has no committed diff). Expect exactly three shapes: fabrication on a
  wrong/split file:line citation, unverified-claim on a test that over-claims
  what it drives (the echo-vs-harness split above), and [UNVERIFIED] items that
  can only be closed by the committed evidence at close time. Fold all three
  into the build; do NOT re-review the plan — the close review judges the built
  code, and the cycle's one review slot is already spent.
- **A missing `cmd:<binary>` on a target host is not a parity failure to paper
  over** — the doctor row reports DEFERRED (harness absent), and the report
  states the parity claim is NOT met on that host rather than claiming it.

## Reference

- `docs/design/fleet-gateway-parity.md` — the 9->0 transport/daemon parity
  matrix and CR5 (pi backend proven live on the second bot).
- `references/harness-session-identity.md` — the env var each harness exposes
  to its MCP children (Claude Code `CLAUDE_CODE_SESSION_ID`, Codex
  `CODEX_SESSION_ID`), and which must be set for the resolver to answer
  "env, unique".
- `ops/install/harnesses/generate-harnesses.py` — `--check` / `--report` /
  (bare) regenerate derived harness docs from the registry.
