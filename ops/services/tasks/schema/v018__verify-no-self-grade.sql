-- ============================================================================
-- tasks schema v018 — designated verifiers may verify NON-OWN work only
--
-- OPERATOR STEER (2026-11-09, after v017):
--   "titus has to ask you for a grant?" → v017's per-slice grant alone is the
--   wrong flow for routine work; the operator chose "the best practical option
--   that doesn't reduce quality": a DESIGNATED verifier (tasks_verifier
--   membership) verifies any review slice EXCEPT ones it created itself —
--   v014's no-self-grading guard survives, and dispatched/fleet work closes
--   with zero asks. Own work still closes via a recorded per-slice grant
--   (v017's authorize_verify), so nothing closes without recorded authority.
--
-- TIER SEPARATION (correction from the battery RED — the CLEAN-reviewed plan
-- collapsed the tiers): v017 de-named is_orchestrator to tasks_verifier
-- membership, so an operator-designated verifier WAS "is_orchestrator" and the
-- no-self-grade branch was dead code (measured: probe granted tasks_verifier
-- verified its OWN slice; transcript in docs/evidence/verify-delegation-
-- 2026-11-09/red-v018-tier-collapse.txt). v018 therefore splits the tiers:
--   * tasks_orchestrator — the CORE tier (moses/esther by default). The
--     meaning of is_orchestrator, restored: core orchestrators verify
--     anything, including their own work (the intelligence layer, v014).
--   * tasks_verifier — the DESIGNATED tier (v017 role, default esther/moses,
--     operator adds via TASKS_VERIFIER_ROLES → fleet-update GRANT every
--     update). Designated verifiers verify NON-OWN work only.
--
-- THE SHAPE — one predicate, both layers
-- v017 taught (ADV-14378-1) that the trigger fires on verify_slice's UPDATE
-- under the CALLER's session_user, so the trigger must carry the SAME rule as
-- the function or the function's own UPDATE is refused. v018 factors the whole
-- "may this caller verify this slice" decision into ONE SECURITY DEFINER
-- predicate, tasks.verify_capable_for(p_role, p_slice_id):
--
--     is_orchestrator(p_role)                            -- core tier (tasks_orchestrator)
--     OR verify_grant_active(p_slice_id, p_role)          -- v017 per-slice grant
--     OR (pg_has_role(p_role, 'tasks_verifier', 'MEMBER') -- designated tier,
--         AND created_by(slice) <> profile_of(p_role))    --   non-own work only
--
-- and both consumers call it:
--   * tasks.verify_slice    — refusal stays a quiet `false`
--   * tasks.check_transition — the review->completed arc stays closed on every
--     write path for anyone the predicate refuses (v015 body otherwise VERBATIM,
--     including the story-precondition early return — the v015 lesson from
--     cycle 14378: never copy from a migration that was applied).
--
-- Branch 2 is INTENTIONALLY not created_by-gated: a verify_grants row exists
-- only after authorize_verify, which is orchestrator-only and records
-- authorized_by + note — the operator-authorized closure route for a
-- verifier's OWN work. It cannot be self-issued.
--
-- WHO BECOMES A VERIFIER IS DATA, NOT CODE
-- The core tier default is moses/esther (below); the OPERATOR designates
-- additional verifiers with the TASKS_VERIFIER_ROLES env var, which
-- fleet-update.sh GRANTs (tasks_verifier only — never tasks_orchestrator)
-- on every update (re-runnable + convergent — a migration runs once, so an
-- env change after apply cannot live here). REVOKE is the recorded
-- revocation.
--
-- Scope NOT changed: review -> in_progress (reject/stale arc) stays open;
-- in_progress -> completed stays open; the story auto-complete early return
-- is untouched. created_by is NOT NULL (v001), so the self-check is
-- unambiguous.
--
-- Docs: docs/design/task-model-v3.md §10-11.
-- ============================================================================

BEGIN;

-- ── 0. The CORE tier role (tier separation — see header) ──────────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tasks_orchestrator') THEN
    CREATE ROLE tasks_orchestrator NOLOGIN;
  END IF;
END $$;

DO $$
DECLARE
  r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['mycortex_reader_esther','mycortex_reader_moses']
  LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('GRANT tasks_orchestrator TO %I', r);
      -- keep the designated-tier membership too (v017 compatibility)
      EXECUTE format('GRANT tasks_verifier TO %I', r);
    END IF;
  END LOOP;
END $$;

-- is_orchestrator = the CORE tier (restored meaning: moses/esther, NOT every
-- verifier). Takes the identity EXPLICITLY (no default — a default
-- CURRENT_USER would silently resolve to the DEFINER inside SECURITY DEFINER).
CREATE OR REPLACE FUNCTION tasks.is_orchestrator(p_role name)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT pg_has_role(p_role, 'tasks_orchestrator', 'MEMBER');
$$;

REVOKE ALL ON FUNCTION tasks.is_orchestrator(name) FROM PUBLIC;

-- Every writer needs EXECUTE: check_transition is INVOKER, so the trigger's
-- call to is_orchestrator runs with the WRITER's privileges.
DO $$
DECLARE
  r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['mycortex_reader','mycortex_admin']
  LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('GRANT EXECUTE ON FUNCTION tasks.is_orchestrator(name) TO %I', r);
    END IF;
  END LOOP;
END $$;

-- ── 1. The single "may this caller verify this slice" predicate ─────────────
CREATE OR REPLACE FUNCTION tasks.verify_capable_for(p_role name, p_slice_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = tasks, pg_temp
AS $$
    SELECT tasks.is_orchestrator(p_role)
        OR tasks.verify_grant_active(p_slice_id, p_role)
        OR (pg_has_role(p_role, 'tasks_verifier', 'MEMBER')
            AND NOT EXISTS (
                SELECT 1 FROM tasks.tasks t
                WHERE t.id = p_slice_id
                  AND t.created_by = tasks.profile_of(p_role)));
$$;

REVOKE ALL ON FUNCTION tasks.verify_capable_for(name, uuid) FROM PUBLIC;

-- The trigger calls it with the WRITER's privileges (INVOKER); verify_slice
-- (SECURITY DEFINER) calls it as owner. Same caller roles v014 granted
-- is_orchestrator to.
DO $$
DECLARE
  r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['mycortex_reader','mycortex_admin']
  LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('GRANT EXECUTE ON FUNCTION tasks.verify_capable_for(name, uuid) TO %I', r);
    END IF;
  END LOOP;
END $$;

-- ── 2. check_transition — one predicate, both layers ───────────────────────
CREATE OR REPLACE FUNCTION tasks.check_transition()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_reason TEXT;
BEGIN
    IF OLD.status IS NOT DISTINCT FROM NEW.status THEN
        RETURN NEW;
    END IF;
    v_reason := NULLIF(current_setting('tasks.transition_reason', true), '');
    -- v015: story → completed from an OPEN state is admitted only when the story
    -- has no active slice — the same precondition the auto-complete path satisfies
    -- by construction. The reason string is NOT consulted: it is caller-settable,
    -- so it can label an event but never authorise a transition. Placed above the
    -- transition matrix because the matrix (v012) admits pending → in_progress/
    -- cancelled/blocked/waiting only, and a never-started story whose slices all
    -- finished legitimately completes from `pending`.
    IF NEW.kind = 'story' AND NEW.status = 'completed'
       AND OLD.status IN ('pending', 'in_progress')
       AND NOT EXISTS (
           SELECT 1 FROM tasks.tasks s
           WHERE s.parent_id = NEW.id
             AND s.status NOT IN ('completed', 'cancelled')
       ) THEN
        RETURN NEW;
    END IF;
    -- v014 (v017 delegation branch; v018 single predicate): the verification
    -- arc is closed on every write path unless tasks.verify_capable_for admits
    -- the caller for THIS slice — orchestrator, designated verifier of work
    -- they did not create, or a live per-slice grant. session_user (never
    -- current_user) is the authenticated caller, which is the identity that
    -- still holds inside verify_slice's SECURITY DEFINER context.
    IF OLD.status = 'review' AND NEW.status = 'completed'
       AND NOT tasks.verify_capable_for(session_user, OLD.id) THEN
        RAISE EXCEPTION
            'review -> completed requires orchestrator verification (caller %): '
            'the generic update path may not approve a slice — verification '
            'needs a moses/esther orchestrator, a designated verifier for work '
            'they did not create, or a recorded per-slice grant',
            tasks.profile_of(session_user);
    END IF;
    -- COALESCE(..., false): transition_allowed must never leak NULL into
    -- the gate (three-valued logic would silently permit the transition).
    IF NOT COALESCE(tasks.transition_allowed(OLD.status, NEW.status, v_reason), false) THEN
        RAISE EXCEPTION 'illegal task transition: % -> % (reason: %)',
            OLD.status, NEW.status, COALESCE(v_reason, 'none');
    END IF;
    IF NEW.kind = 'story' AND NEW.status = 'completed' AND EXISTS (
        SELECT 1 FROM tasks.tasks s
        WHERE s.parent_id = NEW.id
          AND s.status NOT IN ('completed', 'cancelled')
    ) THEN
        RAISE EXCEPTION 'story % has active slices; cannot complete', NEW.id;
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION tasks.check_transition() FROM PUBLIC;

-- ── 3. verify_slice — same predicate; grant consumption unchanged ──────────
CREATE OR REPLACE FUNCTION tasks.verify_slice(
  p_id UUID,
  p_approved boolean,
  p_note TEXT DEFAULT NULL
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = tasks, pg_temp AS $$
DECLARE
  v_updated int;
BEGIN
  -- Orchestrator, designated verifier of non-own work, or the verifier named
  -- in a live per-slice grant. Never trust a self-report: a caller's OWN
  -- work closes only under a recorded orchestrator authorization (the grant
  -- row carries authorized_by + note — the audit trail).
  IF NOT tasks.verify_capable_for(session_user, p_id) THEN
    RETURN false;
  END IF;
  UPDATE tasks.tasks
     SET status = CASE WHEN p_approved THEN 'completed' ELSE 'in_progress' END,
         "column" = CASE WHEN p_approved THEN 'done' ELSE 'in_progress' END,
         status_changed_at = now(),
         plan = CASE WHEN p_approved THEN plan
                     ELSE plan || E'\n-- VERIFY FAILED --\n' || COALESCE(p_note, 'no reason')
                END
   WHERE id = p_id
     AND status = 'review'
  RETURNING 1 INTO v_updated;
  -- Consume the grant on ANY decision it authorizes (approve or reject): one
  -- verification per grant. Rows for slices that never get verified expire.
  IF COALESCE(v_updated, 0) = 1 THEN
    DELETE FROM tasks.verify_grants
     WHERE slice_id = p_id AND verifier = session_user;
  END IF;
  RETURN COALESCE(v_updated, 0) = 1;
END $$;

REVOKE ALL ON FUNCTION tasks.verify_slice(UUID, BOOLEAN, TEXT) FROM PUBLIC;

-- v017 widened EXECUTE to the six profile roles — preserved verbatim (CREATE
-- OR REPLACE keeps the ACL, but re-declaring keeps it explicit).
DO $$
DECLARE
  r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['mycortex_reader_esther','mycortex_reader_moses',
                           'mycortex_reader_titus','mycortex_reader_joseph',
                           'mycortex_reader_kustos','mycortex_reader_gisu']
  LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('GRANT EXECUTE ON FUNCTION tasks.verify_slice(UUID, BOOLEAN, TEXT) TO %I', r);
    END IF;
  END LOOP;
END $$;

COMMIT;
