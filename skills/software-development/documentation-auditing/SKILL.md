---
name: documentation-auditing
version: 1.0.0
category: software-development
description: >-
  Audit documentation for stale file paths, broken cross-references, and
  correctness gaps. Systematic approach for identifying, verifying, and
  reporting stale references in docs directories.
pinned: true
related_skills: [survey-before-action, fleet-preflight, codebase-inspection]
---

# Documentation Auditing

**Systematic approach for auditing documentation correctness — focused on stale file path references, broken links, and out-of-sync doc content.**

Triggers: "audit docs for stale paths", "check documentation for broken references",
"find stale src/ or deploy/ paths in docs", "verify docs are in sync with repo
structure", "run a stale-paths audit", "fresh audit of the docs directory".

## Prerequisites

**Repo layout (verified 2026-09-08 on host esther):**
- The old `src/` tree and the root `deploy/` symlink are **GONE** from this repo — only historical/prose mentions remain (e.g. `core/governance/__init__.py` says the legacy `src/loop-governance/` shim was removed). Treat every live `src/` or bare `deploy/` reference as STALE unless it is prose in a migration doc.
- The deploy tree lives at `ops/install/deploy/` (e.g. `ops/install/deploy/nginx/hermes-services.conf`).
- `docs/DOCS-INDEX.md` exists — Phase 6 checks it.
- There is currently **no** `docs/stale-paths-audit.md` in the repo — if it doesn't exist when you reach Phase 9, CREATE it fresh (use the Phase 8 report structure as its skeleton) instead of assuming a prior audit file exists.

**Required tools:** `search_files` (or `grep`/`rg`), `read_file`, filesystem access to `~/steadfaste-fleet/`. No external network access needed.

## Audit corpus boundary

Search ALL `.md` files under `docs/` (including `docs/prd/` and `docs/runbooks/`), plus top-level `README.md` and `AGENTS.md`. Do not scan `ops/offline/code-corpus/` (third-party snippets, not our docs) or `skills/` (audited separately).

## Workflow

### Phase 1: Identify Stale Path Patterns

Know which paths are known to be stale:

| Old Path | Canonical Path (if known) | Common In-Docs Form |
|----------|--------------------------|---------------------|
| `src/` (root) | `core/` / `ops/` / `mcp-servers/` / `skills/` | `src/scripts/`, `src/mcp-servers/`, `src/skills/`, `src/agent-inbox/`, `src/dashboard/`, `src/offline/`, `src/loop-governance/`, `src/auth/` |
| `deploy/` (root symlink → `ops/install/deploy/`) | `ops/install/deploy/` *(symlink may be removed!)* | `deploy/nginx/`, `deploy/patches/`, `deploy/docker-compose.langfuse.yml` |
| `runtime/` (if applicable) | (varies) | |

**Always verify symlink status on-disk before classifying a `deploy/` ref as "works via symlink"** — the symlink may have been removed since the last audit.

### Phase 2: Search Docs for Each Pattern

For each stale path prefix, search all docs `.md` files:

```bash
# Exclude integration-audit.md (intentional historical record) and stale-paths-audit.md (the report itself)
grep -rn --include='*.md' -E '\bsrc/(scripts|mcp-servers|skills|agent-inbox|dashboard|offline|loop-governance|auth)/' docs/ | grep -v 'integration-audit.md' | grep -v 'stale-paths-audit.md'
grep -rn --include='*.md' -E '(^|[ `(/])deploy/' docs/ | grep -v 'ops/install/deploy/' | grep -v 'integration-audit.md' | grep -v 'stale-paths-audit.md'
```

Note the `--` before the pattern and quoted regexes: an unquoted or un-dashed grep treats a leading-dash pattern as a flag and fails with "unrecognized flag".

Batch the searches — they are independent. Use `search_files` tool with appropriate `file_glob` and `output_mode`. For large docsets (80+ files), consider using `delegate_task` to offload the heavy scanning to a background subagent — this keeps your context uncluttered while the subagent does the exhaustive search and returns a structured report.

**Critical nuance:** Some files may have been **fixed since the last audit**. Keep a running tally of what changed.

### Phase 3: Categorize Matches

Not every `src/` or `deploy/` reference is stale. Classify each:

| Category | Description | Action |
|----------|-------------|--------|
| **STALE — actionable** | A live command/code block/path instruction that will fail if run today | Flag as High/Medium severity |
| **STALE — conceptual** | Template examples, placeholder paths | Flag as Low severity |
| **INTENTIONAL historical** | Migration docs, audit records documenting *what was moved* | Do NOT flag (document the exemption) |
| **ALREADY FIXED** | Content changed since last audit to use correct path | Record as fixed, do not re-flag |

**Distinction for migration/onboarding docs:** Bash code blocks containing `src/` paths are **actionable** — someone could copy-paste them and they'd fail. Prose describing old paths in a historical context is **intentional** and should not be flagged.

### Phase 4: Verify Each Path on Filesystem

For every stale reference found, check the filesystem:

```bash
# Replace <old-path> with the actual stale path found in Phase 2, e.g.:
test -f ~/steadfaste-fleet/src/scripts/foo.py && echo "EXISTS at old path" || echo "NOT FOUND at old path"

# Check canonical/new path
test -f ~/steadfaste-fleet/ops/scripts/foo.py && echo "EXISTS at canonical path" || echo "NOT FOUND at canonical path"

# Check symlinks
test -e ~/steadfaste-fleet/deploy && echo "deploy/ exists (symlink or dir)" || echo "deploy/ GONE — bare deploy/ refs are stale"
ls -la ~/steadfaste-fleet/deploy 2>&1  # confirm whether it is a live symlink
```

Also check the directory's parent to discover what *does* exist there:

```bash
ls <parent-dir-of-canonical-path> 2>&1
```

This uncovers cases like "the file doesn't exist at the stated path and the canonical directory doesn't even have it either."

### Phase 5: Load Previous Audit (if exists)

```bash
find ~/steadfaste-fleet/docs -iname '*stale*path*audit*' -o -iname '*audit*stale*'
```

If the find returns nothing, there is no previous audit — proceed to Phase 6 and note "no prior audit exists" in the report. Do not invent prior findings.

Read it and note:
- Which references were **previously flagged** vs. **newly discovered**
- Which references were **previously described as "works via symlink" but the symlink may now be removed**
- **Severity changes** from previous audit (e.g., low → high because symlink was removed)

### Phase 6: Check DOCS-INDEX.md for Deleted Doc Cross-References

When the previous audit noted a file was deleted (e.g., `docs/agent-inbox-setup.md`):
- Check if `docs/DOCS-INDEX.md` still links to it
- If yes, flag as broken cross-reference

### Phase 7: Check Missing Files Referenced by Docs

Some docs reference files by name without a full path — these could be missing entirely:

```
# In docs
(see fleet-bus-nginx.conf)
```

Search for the file at every plausible location:
```bash
find ~/steadfaste-fleet/ -name '<referenced-filename>' 2>/dev/null
```

### Phase 8: Produce Structured Report

Write the report with these sections:

1. **Summary** — counts: stale found, fixed since last audit, new discoveries, missing files
2. **Previously Identified — Status per item** — table: File, What Was Stale, Status (FIXED / STILL STALE)
3. **Newly Discovered** — table: File, Line, Stale Reference, Correct Path, Severity
4. **Missing Files** — paths that don't exist at any location on disk
5. **Filesystem State** — key paths and their current existence status
6. **Patterns & Recommendations** — grouped by migration pattern with priority

### Phase 9: Apply Fixes and Update Audit Report

After fixing stale paths, update the audit report itself (create `docs/stale-paths-audit.md` if it doesn't exist — as of 2026-09-08 it does not) to reflect what was fixed:

1. Update the **Summary** section to note fixes applied
2. Move items from "STILL STALE" to "ALL FIXED" (with date)
3. Append fix lines in "Newly Discovered" sections (with ✅)

When fixing template/placeholder paths (e.g., `src/auth/middleware.py` in `task-contract.md`), prefer using the `ops/` prefix as the updated example path rather than leaving them stale. Even conceptual examples benefit from being technically accurate.

## Bulk sweeps across many docs (a deprecated construct, a stale claim, a rename)

A sweep applies the same small edit to many files, and the damage a bad sweep does is SILENT: a
half-applied replacement leaves a doc that still renders, still passes every path and link check,
and now teaches something broken or wrong.

- **Script the sweep; never hand-edit file by file.** Enumerate `(file, exact-old, exact-new)`
triples and apply them with a matcher that ASSERTS the old string occurs **exactly once** in that
file before writing, failing loudly on 0 (already fixed, or the wrong text) and on >1 (ambiguous —
pick a longer unique anchor). A fuzzy or context-inferring edit will half-match a line and move on
without telling you.
- **Inline code spans are the fragile case.** When the replacement inserts a parenthetical or a
second command INSIDE backticks, both the opening and the closing mark must survive. A substitution
that adds one backtick and no closing one leaves unbalanced markup that renders as literal garbage
while every automated check still passes. Re-read every changed line whose replacement contains a
backtick, a quote, or a line continuation — before committing, not after review finds it.
- **Leave the WARNINGS alone.** In a sweep for a deprecated construct, the hits that exist to TEACH
the hazard — a portability table, a "do not use X, it silently no-ops here" note, a pitfall list —
are not instances of the problem. Rewriting them "for consistency" deletes the lesson, and it is the
most common way a correct sweep produces a worse document. Classify every hit as RECIPE (fix it) or
WARNING (keep it) before editing a single one.
- **A token with TWO legitimate scopes is not a sweep — it is a CLASSIFICATION.** The same literal
  often names different things in different files, and a blanket replace silently makes half the docs
  wrong: a runtime directory under `$HOME` and a repo-local overlay directory can share a name, so one
  doc's bare reference resolves against the repo while another's resolves against the home dir. Before
  replacing a single hit, decide per hit WHAT IT NAMES, bucket the hits by that, and rewrite only the
  bucket that is wrong — the other bucket keeps the bare form and gains a qualifier (`repo-local`, a
  project path) so it can never be read as the other one. Report the classes you deliberately kept.
- **Never rewrite a value that something PARSES as a matcher or prefix.** When the token appears in a
  config file a hook or checker reads (a path allow-list, a blocklist, a prefix rule), the entry is
  matched against a REPO-RELATIVE subject — making it home-anchored turns a live rule into a silently
  dead one that still passes and authorises nothing. In such a file fix the COMMENT explaining the
  entry, never the entry, and state in that comment which scope the entries are matched in.
- **Prove it by re-grepping for the pattern and reporting the count.** A sweep you did not re-grep
is a claim, not a fix: say "zero remaining" only when a fresh search says so, and quote the count.
**But a raw count is not a pass metric when the fix ADDS annotated mentions of the token.** The edit
that disambiguates a token writes the token again, so the total can go UP while the flawed hits reach
zero. Report the count per class where the classes are separable, and where they are not — an
annotated mention reads like the flaw to any pattern match — say so plainly and NAME the files that
legitimately keep the token, rather than letting a residual count read as work you missed.
- **When the sweep corrects a CLAIM that a shipped change made false, put the measurement in the
same edit.** One clause is enough — why the old sentence was wrong (what was measured, what it
showed) — otherwise the next reader reintroduces it as "obviously true".
- **A replacement that RESTATES the words before its own anchor leaves a visible stutter that the
  exact-match assertion cannot catch.** Anchoring on `... — currently X` and substituting text that
  begins `the current X` produces `— currently / the current X`: the old string matched exactly once
  as required, so every assertion passes and the doc reads as broken. After the sweep, scan the
  written files for duplicated ADJACENT words (`\b(\w+) \1\b`) and read every changed line whose
  replacement shares a word with the anchor — the match count proves uniqueness, never legibility.
- **A count of a live set is stale the moment it is written — delete the number, do not update it.**
  A prose claim like "all 7 required skills" / "the marker tracks 8 names" has already drifted twice
  before you arrive (the file's own history recording a previous mismatch is the tell). Replacing 7
  with 3 buys one more drift, because nothing keeps a comment in step with a `len()`; rewrite the
  claim to be count-free ("all required skills") or derive it at runtime, so the sentence cannot go
  stale again. Sweep every copy of the count, including user-facing block messages, and report the
  re-grep as zero.
- **A DATED snapshot is honest; the same number in PRESENT tense is stale.** A plan, changelog or
  evidence file that labels its own date ("prices fetched 2026-11-13", "state at capture time")
  records what was true then and is NOT stale — rewriting it falsifies the record and breaks any
  hash over it. The identical value asserted with no date, or in a doc describing how the system
  currently works, is stale and must be fixed or dated. State this boundary in the report and NAME
  the files you deliberately left alone, or a reviewer reads the untouched hits as work you missed.

## Pitfalls

- **A "consistency" sweep that rewrites the warnings is worse than no sweep** — see Bulk sweeps
  above: teaching text is not an instance of the problem it teaches about, and neither is a hit that
  names something ELSE (a same-named directory in another scope, a repo-relative guard entry).
- **The `deploy/` symlink may be gone.** Don't assume `deploy/nginx/hermes-services-apply.py` works via symlink unless you've confirmed the symlink exists. Check it every audit.
- **`src/` may still partially exist** (e.g., `src/loop-governance/` kept as backward compat, `src/fleet_bus/__pycache__/` as stale cache). A stale path pointing to a still-extant file is less urgent than one pointing to a deleted file, but it's still stale — the backward-compat copy is unsupported and may be removed. Always verify the **actual source file** exists, not just the directory.
- **Check whether `src/` root exists** — it may be a hollow shell with only `__pycache__` and `.pytest_cache` dirs. `test -d src/` returning true doesn't mean source files are there.
- **Migration docs are not stale.** A file like `docs/migration-*.md` intentionally documents old paths as they were during the migration. Flag only the **actionable code blocks** within them, not the historical prose.
- **Code blocks are actionable.** Even in a migration doc, a ````bash` code block with `bash src/scripts/fleet-update.sh` will **fail** if run. These are High severity.
- **Line numbers shift between audits.** Files get edited. Search by pattern, not by line number from the previous report. A previous report that says "line 586" may now refer to completely different content.
- **DOCS-INDEX.md drifts in BOTH directions.** Deleting a doc leaves the index still linking it; ADDING one the index does not name is the same defect from the other side, and the pre-commit docs audit (`pre-commit-doc-audit.sh`) catches the second with `⚠ DOCS-INDEX.md was not updated` for a new file under a covered dir (e.g. `docs/plans/`). That audit WARNS and lets the commit land, so fix it in the SAME commit: extend the existing row for that DIRECTORY (name the new file plus one clause on what it scopes) rather than adding a new row. **The audit judges the STAGED set**, so an `--amend` that changes the
  doc while its index row already landed in an earlier commit re-fires the warning even though the
  finished commit is correct — before believing it, check that the row names the file in HEAD
  (`grep -c "<basename>" docs/DOCS-INDEX.md`) and that the commit's file list is right
  (`git show --stat`), then proceed. Never reach for `--no-verify` to silence it.
- **A reference by NUMBER is only as stable as the numbering.** A long doc that
  restarts numbering per section (`### 1.` under each topic heading) makes
  "see entry 18" resolve to two or three different places, and an inserted or
  reordered section silently repoints every numbered cross-reference. When you
  touch either side, repoint the REFERRER to the entry's TITLE (`docs/x.md ->
  "<entry title>"`) instead of renumbering the doc: renumbering is a large diff
  that the next append undoes, while a title survives insertion. Sweep every
  numbered referrer in the same pass — code comments, echoed remedy text in
  checks, and prose alike.
- **A printed path is an instruction, and a printed path that cannot resolve is a
  bug in the check that prints it.** When an audit finds a stale path inside a
  script's user-facing output (a remedy line, a "saved to:" message, a `Fix: …`
  hint), fix the message AND confirm the artifact it names actually gets written
  — a message advertising a file nothing creates is worse than silence, because
  the operator goes looking for it.
- **Prove the probe before believing a stale-path finding.** A path-existence
  check that also tests non-paths reports its own error as a broken reference: a
  `file.py::test_name` node id, a command with flags, a `<placeholder>`, a glob,
  or a doc path that is explicitly qualified as living in another repo. Exclude
  those shapes, re-run, and only then report. The mirror case: a hit on the same
  line as an unrelated note is still a hit — printing the hit list in full and
  classifying each line is what keeps the sweep honest.
- **Your OWN edit can invalidate a claim about the file you just edited — audit the describers
  in the same pass.** Adding a pointer or section to `X` makes every doc that DESCRIBES `X`
  stale, and the fragile kind is a NEGATIVE claim (`not documented in X`, `kept out of X`,
  `X does not cover this`): it was true when written and silently becomes false, so no path
  check or link check ever catches it. The moment you extend a file, grep the docs for its
  name and its negative-claim phrasings before you commit:

  ```bash
  grep -rn --include='*.md' -e "$(basename X)" -e 'kept out of' -e 'not documented' -e 'does not cover' docs/ | head
  ```

  Fix the describing row in the SAME commit — a stale claim in the index an implementer reads
  sends them to the wrong place, which is worse than a missing entry.
- **Authorization text goes stale when a name-list becomes a capability (de-naming).**
  Replacing a hardcoded `IN ('moses','esther')`-style allowlist with a role-membership check
  (`pg_has_role(..., 'cap', 'MEMBER')`) makes every AGENT-FACING surface that named the old
  authorities wrong at once — tool descriptions, CLI output strings, docstrings, and skill prose
  all drift independently. This stale kind is the most harmful: it is not merely wrong, it sends
  WORKERS into route-padding (a surviving "only moses/esther may verify" claim makes a designated
  verifier believe it must hand off to an orchestrator nobody needs). When a schema/code change
  de-names WHO may act: (1) read the CURRENT schema/code for the real authority predicate before
  asserting anything; (2) sweep every surface with a SMALL set of distinct stale phrases (the old
  name-list and its "hand to X" fallback typically cover the whole class of hits); (3) re-grep
  after and report zero; (4) pin the corrected wording with a REQUIRED/FORBIDDEN needle guard test
  so a re-introduction fails loudly. A doc or skill that contradicts its OWN later section (an old
  bullet still saying "X allowlist inside the function, refusal is permanent" right beside a
  current "designated verifier needs no grant" block) is the tell that the schema evolved but the
  prose did not — fix the old bullet IN PLACE, never append an "UPDATE:" note under it.
- **An in-code comment ASSERTING a guard exists is a documentation claim — verify the guard exists.**
  `ops/scripts/install/mcp-surface.inc.sh` declared itself "the ONE definition of the fleet MCP
  surface" and, of the registry, "A test pins the two together so neither can drift alone" — and no
  such test existed, so the surface it declared quietly drifted away from what the audit actually
  checked, with nothing to catch it. A comment that names a verification (`pinned by a test`,
  `enforced by the doctor`, `checked in CI`, `asserted at build time`) is auditable like any
  cross-reference: grep for the named test or check, and if it is absent that is a HIGH-severity
  finding, not a wording nit — the comment is then the ONLY thing holding the invariant, and a
  comment is not a control. Fix it by WRITING the guard, then leave the comment true. The same
  follows for a comment asserting a property is impossible; prove it or delete the claim.
- **A comment next to a config VALUE is a claim about that value — it must match the placeholder's
  documented semantics, not a paraphrase that contradicts it.** When you add a line like
  `Environment=X=<placeholder>`, the adjacent comment must describe what the placeholder MEANS per
  the skill/reference that documents it (e.g. the hostname-prefix token, not "the agent's name").
  An independent reviewer files the mismatch as a medium finding (comment contradicts its own
  value), and it is the same drift class the audit exists to catch — now authored by the fix.
  Cross-check the comment against the owning skill's documented placeholder before committing.
- **Editing ONE line inside an example block obliges auditing the WHOLE example.** A config,
  YAML or command block carries an IDENTIFIER (the key, the server name, the env var) that the
  prose beside it and the code consuming it must agree with. Fix the path inside the block and
  leave the key stale and you have shipped an example that registers something the checker
  cannot see — the same drift class the audit exists to catch, now authored by the fix. After
  any edit inside a block: grep its identifier against the code that actually registers or reads
  it, and re-read the sentence next to the block. Where one literal legitimately names two
  things (a retired server name and a live schema name), say which is which IN the block's own
  comment so the next sweep cannot "unify" them.
