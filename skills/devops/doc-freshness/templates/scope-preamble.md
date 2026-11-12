# Scope Preamble — for convention files that breed hesitation

**When:** a repo's AGENTS.md / CLAUDE.md reads like an incident catalog written
as absolutes ("RULE", "Mandatory", "Nothing below is optional") and agents ask
permission before read-only work. Insert the block below after the H1 title,
BEFORE the first rule set, in EVERY surface agents read — AGENTS.md,
CLAUDE.md, AGENTS.seed.md — with identical wording so all harnesses read the
same rule. It fixes the "don't mutate → don't touch" over-generalization: rules
govern changes; investigation is always free.

```markdown
## Scope — Rules Govern Changes, Not Investigation

Every rule in this file governs *changes* — to the repo, to prod, or to
governed state. **Investigation is always permitted without asking:** reading
code, grepping, running local tests, inspecting logs, running read-only
queries. Investigation is subject to the judgment any engineer would apply —
don't exfiltrate secrets, don't quote prod PII in commits or chat, don't run
expensive queries against prod without reason. A rule that calls something
"operator-gated," "deferred," or "orchestrator-only" gates the *mutation*;
research inside that item proceeds normally.

- **Action policy:** Reads are free. Writes go through the gates defined in this file.
  Irreversible or sensitive writes (deploys, schema migrations, credential
  changes) ask first. If unsure whether a write is reversible, treat it as
  irreversible.
- **Claims:** A claim about code or prod state must cite its source (file:line,
  query result, log line). "I think" is not a source. Un-backed claims are
  failures. Backed claims are expected and should not be apologized for.
- **Session handoff:** Record (a) what was decided, (b) what scope it applies
  to, (c) what it explicitly does *not* restrict. Bare labels like
  "operator-gated" without scope are forbidden.

---
```

**Companion — the checkpoint half:** a preamble alone is undone by bare
handoff labels. Every decision/label in a session checkpoint must carry scope:
what it applies to and what it does NOT restrict. A bare "operator-gated"
over-generalizes "don't mutate" → "don't touch" in the next session.
