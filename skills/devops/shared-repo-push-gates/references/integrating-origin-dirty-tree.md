# Integrating origin when the tree is dirty — and proving the deploy followed

Two integration mechanics that block a push after the cycle is closed. Both are about the SHAPE of the
integration, not about the diff being wrong.

## 1. A dirty tree blocks `git pull --rebase` — autostash it, and save foreign diffs first

An uncommitted edit left by another session (or by a pipeline auto-commit that regenerates an
evidence/parity/drift artifact) blocks the rebase before it starts, and blocks the push. Carry it,
never discard it:

```bash
cd ~/steadfaste-fleet
# 1. Preserve the foreign content on disk first — before any checkout can clobber it
mkdir -p ~/.hermes/cache/scratch/foreign-worktree
git diff HEAD -- <foreign path> > ~/.hermes/cache/scratch/foreign-worktree/<name>.diff
git diff HEAD -- <other path>   > ~/.hermes/cache/scratch/foreign-worktree/<other>.diff
# 2. Integrate, carrying the uncommitted work
#    (the fleet's own sync tool uses --rebase; the gate itemises ahead/behind first)
git fetch origin && git -c rebase.autoStash=true pull --rebase
```

Rules that go with it:

- **Keep foreign files OUT of your commit.** `git commit --only -F <msg> -- <your paths>` with an
  explicit pathspec is what does that; a bare `git add -A` sweeps them in and lands work you did not
  author. Report them as a loose end (with the scratch diff paths) instead of landing or reverting them.
- **A conflicted autostash pop reports `UU` on one file.** When that file is PIPELINE-MANAGED (a
  generated evidence/parity file that an auto-commit regenerates), take HEAD's copy:
  `git checkout HEAD -- <file>`. Hand-merging a generated file produces content no generator would
  emit, and the next regeneration re-flags the pair.
- **A conflicted pop is not a failed rebase.** The output says "successfully rebased" and leaves the
  stash intact: your rebased commit is already on the new tip, and the stash entry is a duplicate of
  what you re-applied. Do not `git stash drop` until the tree state is what you want.
- **A dirty tree does NOT block the bare push itself** — the push gate reads the range, not the
  worktree. Do not delete foreign work to "clean up" for a push.

## 2. The push is a LONE command — the gate cannot prove a compound shape

The gate refuses a push carrying any separator, pipe, redirect or background operator
(`&&`, `| tail`, `>`, `&`), even when a CLEAN receipt covers the range: it can only prove the
receipt authorises the range when the push is the whole command. So:

```bash
git push origin main          # alone, nothing after it
```

and verify the landing afterwards as separate lock-free reads:
`git log --oneline -1 origin/main && git rev-parse HEAD origin/main`.

A refusal that names the receipt is not a licence to open a publish cycle — re-issue the push alone
and read what it says.

## 3. A rebase that pulls peer commits makes YOUR deploy stale — re-deploy and PROVE it

If the deploy ran BEFORE the rebase integrated origin, the peer's files are in the repo and not in the
deployed tree. That shows up as a doctor warning or a `Deploy sync ❌` at push time.

- Re-run the deploy in its OWN cycle (`begin_change` → `bash ops/scripts/fleet-update.sh` → close):
  a deploy is a change, and the doctor's rows are the evidence for it.
- **Prove the deploy landed per file with an empty diff**, not by re-running the deploy and asserting
  success:

```bash
diff -q ~/steadfaste-fleet/skills/devops/<skill>/SKILL.md \
        ~/.steadfaste-fleet/skills/devops/<skill>/SKILL.md      # no output + rc 0 = in sync
```

Choose the file that was DIFFERENT before the deploy as the check — a file the deploy never touched
proves nothing. Note that `diff -q` and interpreter runs are write-class to the terminal classifier;
a lone `diff -q` or a cycle-held deploy is how you run them without tripping the gate.
