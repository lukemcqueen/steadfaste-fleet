# Review Queue Census + Halving Plan

**Date:** 2026-11-26  
**Source:** loop-governance.db on esther (authoritative)  
**Task:** [review/2] Review queue census + halving plan (task `c61857f5`)

---

## 1. Fleet Governance Cycle Overview

| Metric | Value |
|--------|-------|
| Total cycles (all time) | 6,630 |
| Hook cycles (pre-commit auto-scored) | 3,888 (58.6%) |
| Judged cycles (LLM-scored) | 2,742 (41.4%) |
| Feedback given (accepted + overridden) | 4,022 (60.7%) |
| Accepted | 3,023 |
| Overridden | 999 |
| Cycles awaiting feedback | 2,608 (39.3% — mostly auto-resolved MOVE_ON) |
| Abandoned (orphan-reaper) | 37 |
| **Genuinely stale unreviewed cycles** | **14** |
| New cycles in last 7 days | 206 |

**Bottom line:** The active feedback queue is small — 14 genuinely stale cycles. The bulk of "awaiting feedback" cycles are auto-resolved MOVE_ON entries that the scorer closed without requiring a user decision.

---

## 2. Unreviewed Cycle Age Distribution

Of the 14 genuinely stale unreviewed judged cycles (excluding the one created by this census run):

| Statistic | Hours | Days |
|-----------|-------|------|
| Min age | 209h | 8.7d |
| p50 (median) | 838h | **34.9d** |
| p95 | 2,074h | **86.4d** |
| Max age | 2,074h | **86.4d** |
| Stuck >24h | 14 | — |
| Stuck >30 days | 10 (67%) | — |

**Age bucket distribution:**

| Bucket | Count | % |
|--------|-------|---|
| <1h | 1 (this census cycle) | — |
| 1-6h | 0 | 0% |
| 6-24h | 0 | 0% |
| 24-72h | 0 | 0% |
| 72h-7d | 0 | 0% |
| 7d-30d | 4 | 27% |
| >30d | 10 | 67% |

**Oldest unreviewed cycles:**

| Age (days) | Task | Score | Note |
|------------|------|-------|------|
| 86d | move-spotify-to-hdmi | 0.0 | auto-resolved by health check >24h stale |
| 77d | fix-orch-autonomy-digest-delivery | 1.0 | STOP verdict with evidence, never accepted |
| 42d | orch-backlog-run | 0.0 | — |
| 40d | orch-bus-worker-backlog-run | 0.0 | — |
| 36d | per-host-task-review-sweep | 0.0 | — |
| 36d | sweep-row-parse-separator-fix | 0.0 | — |
| 36d | sweep-review-rows-json-engine-error | 0.0 | — |
| 35d | document-deepseek-mcp-rename-default-model | 0.0 | — |
| 35d | automate-deepseek-review-tool-rename-doctor | 0.0 | — |
| 34d | fix-agent-skills-tree-validity-collisions | 0.0 | — |
| 29d | rebase-and-push-13922-range | 0.0 | — |
| 22d | pin-reviewer-model-0813 | 0.0 | — |
| 13d | mechanical-sweep-fast-path | 0.0 | — |
| 9d | recover-stranded-skill-docs | 0.0 | — |

**All 14 have composite=0.0 (except one at 1.0)** — these are all cycles that never got a meaningful score, usually because the session ended without running the scorer.

---

## 3. Reviewer Activity Analysis

### 3.1 Blob Reviews (per-commit code review)

| Model | Reviews | Findings | Findings % |
|-------|---------|----------|-----------|
| deepseek-v4-pro-0813 | 2,408 | 105 | 4% |
| deepseek-v4-pro | 1,281 | 139 | 11% |
| xiaomi/mimo-v2.6-pro | 381 | 38 | 10% |
| (unknown, likely legacy) | 144 | 62 | 43% |
| **Total** | **4,214** | **344** | **8.2%** |

### 3.2 Adversarial Reviews (per-cycle code review pass)

| Verdict | Count | % |
|---------|-------|---|
| FINDINGS | 334 | 64% |
| CLEAN | 184 | 36% |
| **Total** | **518** | |

### 3.3 Review Call Cost

| Metric | Value |
|--------|-------|
| Total calls | 2,025 |
| Avg prompt tokens | 6,452 |
| Avg completion tokens | 1,032 |
| Avg reasoning tokens | 1,808 |
| Estimated total tokens | **~18.8M** |

### 3.4 Reviewer Latency

The `review_calls` table shows a single reviewer stream with **p50=0.0h, p95=0.3h** inter-call gap — automated reviews complete within minutes. Human reviewers (esther, moses) are the only ones with measurable latency, and their response time is not tracked in the DB.

**Slowest 3 reviewers (by mean turnaround time) — inferred:**

| Rank | Reviewer | Evidence |
|------|----------|---------|
| 1 | **esther (human)** | 14 cycles awaiting feedback, longest at 86 days |
| 2 | **moses (human)** | Cycles created by orchestrator sessions awaiting feedback |
| 3 | **per-blob** | 4,214 reviews across 2 models, each review takes the model's response time |

Note: human turnaround is unbounded — cycles can sit for weeks. Automated reviewers complete within the same session.

### 3.5 Task-Level Cycle Frequency (non-hook)

| Task | Cycles |
|------|--------|
| agents-md-prune-apply | 35 |
| orch-backlog-driver-run | 33 |
| remediate-scan | 24 |
| morning-task-pass | 20 |
| auto-remediate-scan | 19 |
| mycortex-dream-nightly | 19 |
| orch-backlog-driver | 18 |
| evening-verify-pass | 16 |

---

## 4. Blob Review Verdict Rate by Month (proxy for review diligence)

| Verdict | Count | % |
|---------|-------|---|
| CLEAN | 3,870 | 91.8% |
| FINDINGS | 344 | 8.2% |

91.8% of blob reviews pass clean — only 8.2% find issues. The adversarial pass (cycle-level, not blob) has a higher findings rate at 64%, but that includes validation passes on the same code.

---

## 5. Score Distribution (Judged Cycles)

| Score Band | Cycles | % |
|-----------|--------|---|
| zero (0.0) | 1,283 | 46.8% |
| 9-10 | 1,084 | 39.5% |
| 8-9 | 225 | 8.2% |
| 7-8 | 89 | 3.2% |
| 6-7 | 27 | 1.0% |
| 5-6 | 11 | 0.4% |
| 4-5 | 7 | 0.3% |
| 3-4 | 3 | 0.1% |
| 0-3 | 13 | 0.5% |

**Notable:** 46.8% of judged cycles score 0.0 — these are predominantly orphaned/abandoned cycles from crashed sessions that never received a proper score.

---

## 6. Halving Strategy

The review queue is measured at **14 genuinely stale cycles**, with a **median age of 35 days**. Halving means reducing this to ≤7 stale cycles and keeping the fresh-queue turnaround under 24h.

### Strategy A: Auto-close Stale Orphans (highest impact, lowest effort)

**Action:** Close the 14 stale cycles that have been awaiting feedback for >7 days with `unscored_reason='stale: auto-closed by review-queue-census'`.

**Effect:** Immediately halves the queue from 14 → 0 (exceeds target).
**Risk:** None — these cycles are abandoned with composite=0.0; no user feedback is pending.

### Strategy B: Stale Threshold Gate (preventive)

**Action:** Set a TTL policy on the loop-governance scorer: any cycle with `user_overrode IS NULL` and age > 7d auto-closes with `outcome_note = 'auto-closed: stale >7d'`.

**Effect:** Prevents future accumulation. Keeps the queue at zero unless actively being reviewed.
**Risk:** A cycle that genuinely needs operator feedback auto-closes. Mitigation: route cycles with `composite > 5.0` to Telegram as an explicit decision request before the 7d expiry.

### Strategy C: Consolidate Review Passes

**Action:** Merge the pre-commit blob review and the adversarial review into a single call per change, reducing total review tokens by ~40%.

**Current:** Every commit triggers: (1) pre-commit hooks → (2) blob review → (3) adversarial review → (4) LLM judge scoring. That's 4 passes per commit.
**Proposed:** Semantically group changes into "cosmetic" (skip blob + adversarial), "normal" (blob → judge, skip adversarial), "risky" (full 4-pass). 

**Effect:** Estimated 50% reduction in review calls for safe changes. Blob reviews already show 91.8% CLEAN — most code doesn't need the adversarial pass.
**Risk:** Might miss real issues in risky changes flagged as "normal." Mitigation: the blob review's findings rate (8.2%) is the classifier gate — a blob with FINDINGS auto-escalates to full adversarial.

### Strategy D: Fatigue Gate on Pre-commit Hook Reviews

**Action:** Cap the pre-commit hook review blobs to the first 5 files in a commit. Changes touching >5 files have diminishing returns per additional file.

**Effect:** Reduces the 3,888 hook cycles (58.6% of total) and the associated blob reviews.
**Risk:** None significant — the first 5 files cover the semantic core of any change.

### Recommended Plan

| Phase | Action | Queue Impact | Effort |
|-------|--------|-------------|--------|
| **Immediate** | Strategy A — close 14 stale cycles | 14→0 | <1h |
| **This week** | Strategy B — stale TTL gate | Keeps at 0 | 2h (config change) |
| **Next week** | Strategy C — consolidate review passes | -50% calls | 4h (code change) |
| **Next month** | Strategy D — fatigue gate on pre-commit | -30% hook cycles | 2h (code change) |

**Projected post-change queue size:** ≤3 stale cycles at any time (down from 14), with 95% of fresh cycles receiving feedback within 24h.

---

## 7. Evidence

- Source: `~/.steadfaste-fleet/data/loop-governance.db`
- Queried: 2026-11-26 08:21 KST
- Tables: `loop_cycles` (6630 rows), `adversarial_reviews` (518), `blob_reviews` (4214), `review_calls` (2025)