# 2026-11-14 — The Week the Answer Had to Come From the Live Copy (weekly)

agent-mycortex-dream-weekly (weekly-2026-11-14) [2026-11-14 05:45 KST]
-------------

**Phase 1 — Fresh pages: 5 pages surfaced from the week** (from `mycortex list -n 30`)
- `hermes-cortex` → `ops/install/deploy/nginx/hermes-security` (2026-11-13 17:16)
- `hermes-cortex` → `skills/devops/enforcement-change-safety/SKILL.md` (2026-11-13 17:01)
- `hermes-cortex` → `ops/scripts/fleet-update.sh` (2026-11-13 17:01)
- `hermes-cortex` → `tests/test_begin_change_dogfood_scoping.py` (2026-11-13 16:31)
- `hermes-cortex` → `docs/rename-followups-steadfaste-fleet.md` (2026-11-13 11:01)
- `esther` → `bible/james-2026-11-14.md` (2026-11-13 16:16) — this week's reading, key verse James 1:19

**Phase 2 — Connections mapped: 3 threads linking the week's work**
- Thread 1: **the rename tail re-anchoring names** — `search "env namespace rename fallback"` ranks `skills/devops/rename-sweep/references/env-namespace-rename.md` (0.998) and `docs/runbooks/cortex-to-fleet-env-sweep.md` (0.257); the `CORTEX_*` → `FLEET_*` family landed 2026-11-12/13, and `docs/rename-followups-steadfaste-fleet.md` records what must stay old ON PURPOSE. Same object as the 11-07 weekly.
- Thread 2: **the gate scoped to its own house** — `search "dogfood gate scoping"` lands `tests/test_dogfood_gate_scoping.py` (0.9999), `tests/test_begin_change_dogfood_scoping.py` (0.996) and `enforcement-change-safety/references/leaked-cycles-and-doctor-grep-2026-08-05.md` (0.996): the gate now fires only in the repo it protects.
- Thread 3: **the proof must be re-runnable** — `search "runnable evidence proof close"` lands `docs/evidence/weekly-dream-content-2026-11-07.md` (0.739), `dreams/2026-11-07-weekly.md` (0.739) and `governance-closeout/SKILL.md` (0.737) — the close wants an executed verifier, not a transcript.
- Cross-check: `search "stale reference rename"` returns the 11-07 weekly (0.809) and `documentation-auditing/SKILL.md` (0.778) — the naming law, one week on.
- Gap noted (honest, not fabricated): the `esther` source still indexes under the page-source label `hermes-cortex` — the OLD name — one more stale reference; and the newest lesson files are dated **2026-11-06 02:02**, so no lesson batch exists for 11-07…11-14 (reported in Phase 3).

**Phase 3 — Lessons synthesized: 4 of the freshest lesson files share one root**
- `20260825-001252-c193a700` — `_resolve_peer_id` returned the psql column header `"id                  "` — the header row was read as the value.
- `20260831-183017-149ae749` — the DEPLOYED copy still carried a peer's stashed edit; the deploy order was wrong ("deploy AFTER stashing"), so the check measured the wrong copy.
- `20261014-195745-cd0e365b` — a verdict parser assumed a JSON shape the call never promised; it had to become tolerant and assert the raw text.
- `20261014-092609-eb3d118f` — a cron's live prompt held wrong SQL (a column that does not exist, an ambiguous id); and a config migration "didn't fully land on Titus".
- Same class, every one: **the answer was read from the wrong copy or the wrong position** — the header instead of the value, the deployed tree instead of the repo, the assumed shape instead of the raw text, the stale job definition instead of the live one. This is `2026-10-27`'s "The Answer That Was Read From the Wrong Copy" and `2026-11-07`'s weekly law — *the verdict came from an instrument misaligned with the live referent* — surfacing again, now in the name/referent vocabulary.
- Note: no lesson files are dated 2026-11-07…11-14 (last batch 2026-11-06), so this synthesis draws on the newest available batch; the gap itself is the week's quiet finding.

**Phase 4 — Scripture connection (bible dir exists):** This week's reading closes on **James** (2026-11-14), key verse James 1:19 — *"let every person be quick to hear, slow to speak."* Its load-bearing line is James 2:17: *"faith by itself, if it does not have works, is dead"* — **πίστις (pistis)** answered by **ἔργον (ergon)**, trust made visible in a deed. Hebrews 10:23 (2026-11-13) adds the other half: *"hold fast the confession of our hope without wavering, for he who promised is faithful."* The week's engineering is the same shape: a name, a fallback, a gate that never touches the live referent is dead the way faith without works is dead. Its proof is the executed run, not the label — why the dream itself must ship a verifier, not a transcript.

**Phase 5 — Dream:**

This week the brain kept reading one question in two vocabularies: *does the thing you named point at the thing that is?*

The rename tail closed — `cortex` → `fleet`, the `CORTEX_*` env family renamed in release 2, and `docs/rename-followups-steadfaste-fleet.md` recording what must stay old on purpose. At the same time a gate learned its own address: `test_begin_change_dogfood_scoping.py` proves the dogfood gate fires only in the house it guards. A name and a gate are the same law from opposite sides — each means nothing except what it points at.

The freshest lessons agree. A peer-id resolver read the psql header row, `id                  `, as if it were the value. A deploy copied a stashed peer edit into the deployed tree, so the check measured the wrong copy. A verdict parser assumed a JSON shape the call never promised. Not one was fixed by softening the claim; every one was fixed by measuring the live referent.

James 2:17 ties it shut: *faith apart from works is dead*. A claim without its executed proof is faith without works — it names a thing and never touches it. That is why the dream must ship a runnable verifier and not a transcript.

*James 2:17 — faith apart from works is dead.*

**Phase 6 — Written back:** `~/brain/esther/dreams/2026-11-14-weekly.md` (+ `INDEX.md` appended)

Result: 1 weekly dream written to brain and delivered.

---
*Tier 2 weekly mycortex dream — synthesized from `mycortex list -n 30`, four real `mycortex search` runs, 4 lesson files, `docs/rename-followups-steadfaste-fleet.md`, and James 2:17 / Hebrews 10:23. Builds on `2026-11-07-weekly` (the names learned where home was), `2026-10-27` (the answer read from the wrong copy) and `2026-11-14` (the gate that fired in the wrong house). Fix applied this run: repointed the broken `~/.local/bin/mycortex` symlink from the decommissioned `~/.hermes-cortex` to `~/.steadfaste-fleet/scripts/mycortex`.*
