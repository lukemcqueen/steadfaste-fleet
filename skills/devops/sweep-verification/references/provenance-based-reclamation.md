# Provenance-based reclamation — cleaning up after a deploy

How to make an automated cleanup safe enough to run unattended, and why the
obvious version cannot be. Use this when a deploy/installer leaves files behind
after renames and removals, or when a cleanup already exists and keeps missing
things.

## Why a scan cannot be made aggressive

A cleanup that scans the managed tree for "files not in the register/ownership
map" cannot distinguish:

* a destination WE deployed and later renamed or retired — safe to reclaim, and
* a file that exists only on this host — must never be touched.

They look identical from a scan. Tuning the heuristic harder loses more of the
second kind (this has destroyed a host-local script before), so the correct
response to that ambiguity is to stop deleting — which is where most codebases
end up, with a warn-only cleanup and orphans accumulating forever. The fix is
not a better heuristic; it is giving the cleanup MEMORY.

## Ownership = recorded writes

Have the deploy record every path it wrote, then:

```
orphans = (paths in the PREVIOUS inventory) - (paths in THIS deploy)
```

That set is by construction *files we provably created and no longer create*.
Anything absent from the previous inventory was never ours, so host-local files
are safe STRUCTURALLY rather than by a tuned guess. State the guarantee that
way in the code comment — "untouched by construction" is the property that lets
this run unattended.

## Inventory mechanics

* One writer. The reclaimer tool (not the deploy script, not a second helper)
  reads the existing inventory as OLD, reclaims, then writes the new one — so
  read and write cannot disagree.
* Atomic replace: write a temp file, `os.replace()` onto the inventory.
* Archive the previous list (`<inventory>.prev`) for audit; a reviewer asking
  "how do you know what you owned last time" should have a file to read.
* Store it outside the swept tree's own scope of interest (e.g. under `state/`)
  so it is never itself an orphan.
* Only advance the inventory when the reclaim SUCCEEDED. A partial failure that
  still advanced the record makes the missed orphan permanently invisible.

## Reclaim mechanics

* QUARANTINE, never delete: move to `<quarantine>/<utc-timestamp>/<relative-path>`
  and PRINT the exact restore command (`mv '<dest>' '<src>'`). Unattended
  destructive steps default to no-op; deleted data cannot be remediated.
* Preserve the relative path inside the quarantine stamp dir so a basename
  collision cannot make a restore ambiguous.
* Skip entries that no longer exist (`already gone`), and say so.
* Fail closed with a distinct exit code the caller can act on; a status the
  deployer can key off beats a traceback.

## Path hygiene (the bug that hides here)

* Normalise LEXICALLY (`os.path.abspath(os.path.normpath(p))`), never
  `.resolve()`. Resolving follows symlinks, so an entry that is an intentional
  `hook -> registered-script` symlink becomes its TARGET and the reclaim moves a
  live file. Normalise BOTH sides (entry and managed root) the same way.
* Containment check: the entry must be under the managed root; discard anything
  else rather than defending against it later. This is what makes a corrupt or
  hostile inventory harmless.
* Detect symlinks separately (`is_symlink()`) and SKIP them — a link's target is
  not yours to move. Report the skip; do not silently pass.
* Keep a name-prefix escape hatch (e.g. any `local-*` file) as belt-and-braces,
  purely so a future bad inventory still cannot reach one. It cannot legitimately
  appear in an inventory if only registered destinations are recorded.

## Scope — what counts as "ours"

The register/ownership map is the ONE definition of what a deploy covers. Build
the inventory from that map, evaluated for THIS host, restricted to paths that
exist — never from "everything in the tree that looks deployed".

If the map has a host-conditional section (orchestrator-only, role-specific),
that condition is host-derived and must gate the inventory too — otherwise a host
that never deployed those paths can end up recording someone else's file as its
own, which later makes it reclaimable. Extract the condition into ONE named
helper when a second caller appears; a third inline copy of the same rule is how
it drifts.

## Wiring

* Run it on the COMMON path. A maintenance step wired only into a rare mode
  (`--force-all`, `--repair`, a manual flag) is effectively dead code — check the
  call site, not just the function body, and assert it in a test.
* A failure DEFERS the update (recorded, surfaced at the end) rather than passing
  green. Provenance that rots silently is worse than a loud failure.
* Give it a `--dry-run` so the first exercise can be reported without acting.
* Extract the logic into a standalone, argument-driven helper rather than burying
  it in a large deploy script: then it is unit-testable, and a dash-named file is
  still importable in tests via
  `importlib.util.spec_from_file_location(name, path)`.

## Bootstrap (do not skip this)

An inventory cannot know files deployed BEFORE it existed. So:

* First run with no inventory must be an explicit NO-OP that says so. It does
  write the baseline list — that first write is the memory every later run needs.
* Pre-existing strays have no provenance and are therefore the OPERATOR's call,
  not an auto-quarantine. Report them and offer them as a candidate set (a
  doctor/audit check's "stale deploy" list is a good one). Sweeping them
  automatically is the same guess the whole design exists to avoid.

## Test checklist

The negative cases are the tests that matter:

1. Missing/empty previous inventory -> nothing swept, baseline written.
2. A file in neither inventory (host-local, operator-edited) survives.
3. `local-*` survives even if an inventory claims it.
4. A path outside the managed root is refused (hostile/corrupt inventory).
5. A symlink entry is skipped and its target survives.
6. Orphan is quarantined with content intact; relative path preserved; restore
   command printed and shown to work.
7. `--dry-run` moves nothing.
8. A failed move leaves the inventory un-advanced.
9. Grep-level: the caller invokes the mechanism OUTSIDE the rare-mode block, and
   emits/points at the inventory path. Bound source windows by the next top-level
   `def`, never a character count.

Also run the WIDER selection (`pytest -k "doctor or deploy or stale"`) — changing
remedy text in a doctor check can break a grep-based test elsewhere that asserts
on the old wording.

## Check the remedies you are leaving behind

If a doctor/audit check detects the same orphans, fix its REMEDY to match: a
prescription saying `rm <file>` with no recovery path contradicts this design and
is the same defect class as a remedy whose command cannot run. And once the
inventory exists, the check should READ it rather than re-deriving the register
map by parsing the deploy script — two independent parsers of the same map are a
mirror that drifts.
