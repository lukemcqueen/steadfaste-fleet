-- ============================================================================
-- tasks schema v017 — delegated verification: capability role + per-slice grants
--
-- THE HOLE THIS CLOSES (reported by Titus Pi, 2026-11-08, gate-safety note)
-- `tasks.verify_slice` needs a moses/esther session inside the host's OWN
-- Postgres: is_orchestrator() is a hardcoded ('moses','esther') name list and
-- EXECUTE is granted to those two profile roles only (v014). The tasks store is
-- HOST-LOCAL — a worker host has no orchestrator identity at all — so a slice
-- reported to `review` on a worker host sat there forever. Titus worked around
-- the dead-end with a recorded-and-reversed temporary allowlist change (ran
-- verify_slice with an operator-authorization note, restored the strict gate,
-- confirmed is_orchestrator('mycortex') = false again; nothing left open).
-- Operator directive: fix the durable gap at the source — the v009/v014 EXECUTE
-- grant + the is_orchestrator allowlist.
--
-- THE FIX — two separable defects:
--   A. DE-NAME the allowlist (standing principle: capability, not names).
--      is_orchestrator becomes pg_has_role(p_role, 'tasks_verifier', 'MEMBER').
--      The new NOLOGIN role tasks_verifier is granted by default to the two
--      orchestrator reader roles (IF EXISTS, per-host safe) — behaviour is
--      unchanged on every current host, and "add a permanent verifier" is now
--      `GRANT tasks_verifier TO <role>` — data, queryable + revocable, never a
--      schema edit. The function NAME is kept (the trigger and SECURITY DEFINER
--      bodies call it); a comment marks the meaning.
--   B. DELEGATED VERIFICATION — the orchestrator RECORDS an authorization row;
--      the worker's own verify_slice call honors it for THAT slice only. The
--      row is slice-scoped (PK), verifier-scoped, short-lived (1h default),
--      ONE-SHOT (consumed on the first verify decision, approve or reject), and
--      audited by authorized_by + a REQUIRED note. A worker can never verify a
--      slice it was not granted, and never a second time: self-graded work
--      still cannot wear the same status as verified work without a recorded
--      orchestrator authorization — which is exactly the audit trail Titus was
--      approximating by hand.
--
-- WHY verify_grants has NO grants and the trigger needs a helper
-- check_transition() is SECURITY INVOKER and now must consult the grant row
-- (it fires on verify_slice's UPDATE under the CALLER's session_user — an
-- untouched v014 identity check would refuse the granted worker's own verify).
-- A trigger subquery on verify_grants would raise `permission denied for table
-- verify_grants` under the worker's privileges, so the lookup lives in
-- tasks.verify_grant_active(), a SECURITY DEFINER visibility helper (the rule:
-- policy/trigger subqueries evaluate with the CALLER's privileges). EXECUTE on
-- the helper is granted to the writer roles, mirroring v014's is_orchestrator
-- grants.
--
-- SCOPE — what is deliberately NOT gated
-- Unchanged: review -> in_progress (the reject/stale arc — asserts nothing
-- about verification; the 24h stale sweep re-queues through it); in_progress
-- -> completed (worker finishing its own work); the story auto-complete early
-- return. grant rows on slices that never get verified simply expire — a row
-- that cannot authorize (wrong state, wrong verifier, expired) is inert.
--
-- Docs: docs/design/task-model-v3.md §3.
-- ============================================================================

BEGIN;

-- ── 1. The capability role (de-named allowlist) ─────────────────────────────
-- CREATE ROLE has no IF NOT EXISTS — idempotent DO block.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tasks_verifier') THEN
    CREATE ROLE tasks_verifier NOLOGIN;
  END IF;
END $$;

-- Default members: the orchestrator profile roles. Guarded (IF EXISTS) — a host
-- that does not create those roles keeps the strict no-verifier default, and a
-- role that does not exist would fail the migration (v001's rule).
DO $$
DECLARE
  r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['mycortex_reader_esther','mycortex_reader_moses']
  LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('GRANT tasks_verifier TO %I', r);
    END IF;
  END LOOP;
END $$;

-- ── 2. is_orchestrator = holder of the tasks_verifier capability ───────────
-- Previously `profile_of(p_role) IN ('moses','esther')` — a literal name list
-- baked into the deciding code. Now a membership check: who-may-verify is data
-- (role grants), queryable and revocable, and adding a verifier is a GRANT,
-- never a schema edit. Takes the identity EXPLICITLY (no default — a default
-- CURRENT_USER would silently resolve to the DEFINER inside SECURITY DEFINER).
CREATE OR REPLACE FUNCTION tasks.is_orchestrator(p_role name)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT pg_has_role(p_role, 'tasks_verifier', 'MEMBER');
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

-- ── 3. Per-slice delegation records ─────────────────────────────────────────
CREATE TABLE tasks.verify_grants (
    slice_id      uuid PRIMARY KEY,
    verifier      name NOT NULL,
    authorized_by name NOT NULL,
    note          text NOT NULL CHECK (length(btrim(note)) > 0),
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    CHECK (expires_at > created_at)
);

REVOKE ALL ON TABLE tasks.verify_grants FROM PUBLIC;
-- No grants to any role: the SECURITY DEFINER functions are the ONLY writers
-- (authorize_verify inserts, verify_slice consumes) and reads go through the
-- visibility helper. Audit reads by a DB owner are unaffected (owner rights).

-- ── 4. Visibility helper — SECURITY DEFINER, callable in INVOKER contexts ──
CREATE OR REPLACE FUNCTION tasks.verify_grant_active(p_slice_id uuid, p_role name)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = tasks, pg_temp
AS $$
    SELECT EXISTS (
        SELECT 1 FROM tasks.verify_grants g
        WHERE g.slice_id = p_slice_id
          AND g.verifier = p_role
          AND g.expires_at > now()
    );
$$;

REVOKE ALL ON FUNCTION tasks.verify_grant_active(uuid, name) FROM PUBLIC;

-- The trigger calls it with the WRITER's privileges (INVOKER); verify_slice
-- (SECURITY DEFINER) calls it as owner — the owner always holds EXECUTE.
DO $$
DECLARE
  r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['mycortex_reader','mycortex_admin']
  LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('GRANT EXECUTE ON FUNCTION tasks.verify_grant_active(uuid, name) TO %I', r);
    END IF;
  END LOOP;
END $$;

-- ── 5. authorize_verify — the orchestrator records a delegation ─────────────
-- Orchestrator-only (silent 'f' to everyone else, like verify_slice — never
-- reveal the delegation surface). Validation FAILS LOUD (RAISE) for an
-- orchestrator's bad input: a non-string verification note, a verifier role
-- that does not exist, or a slice that is not in `review` (fail-closed — a
-- grant on a live/closed slice does nothing, so it is refused up front).
CREATE OR REPLACE FUNCTION tasks.authorize_verify(
  p_slice_id   uuid,
  p_verifier   name,
  p_note       text,
  p_expires_at timestamptz DEFAULT now() + interval '1 hour'
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = tasks, pg_temp AS $$
DECLARE
  v_status text;
BEGIN
  IF NOT tasks.is_orchestrator(session_user) THEN
    RETURN false;
  END IF;
  IF p_note IS NULL OR length(btrim(p_note)) = 0 THEN
    RAISE EXCEPTION 'authorize_verify: a note is required (recorded reason for the authorization)';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = p_verifier) THEN
    RAISE EXCEPTION 'authorize_verify: verifier role % does not exist', p_verifier;
  END IF;
  SELECT status INTO v_status FROM tasks.tasks WHERE id = p_slice_id;
  IF v_status IS NULL THEN
    RAISE EXCEPTION 'authorize_verify: slice % does not exist', p_slice_id;
  END IF;
  IF v_status <> 'review' THEN
    RAISE EXCEPTION 'authorize_verify: slice % is % (a grant is only for reported work in review)', p_slice_id, v_status;
  END IF;
  INSERT INTO tasks.verify_grants (slice_id, verifier, authorized_by, note, expires_at)
  VALUES (p_slice_id, p_verifier, session_user, btrim(p_note), p_expires_at)
  ON CONFLICT (slice_id) DO UPDATE
     SET verifier      = EXCLUDED.verifier,
         authorized_by = EXCLUDED.authorized_by,
         note          = EXCLUDED.note,
         expires_at    = EXCLUDED.expires_at;
  RETURN true;
END $$;

REVOKE ALL ON FUNCTION tasks.authorize_verify(uuid, name, text, timestamptz) FROM PUBLIC;

-- Widened like verify_slice: every profile role may CALL; the in-function
-- orchestrator check (session_user) is the real gate and answers 'f' silently.
-- The two-layer shape mirrors verify_slice's (callable / refused inside).
DO $$
DECLARE
  r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['mycortex_reader_esther','mycortex_reader_moses',
                           'mycortex_reader_titus','mycortex_reader_joseph',
                           'mycortex_reader_kustos','mycortex_reader_gisu']
  LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('GRANT EXECUTE ON FUNCTION tasks.authorize_verify(uuid, name, text, timestamptz) TO %I', r);
    END IF;
  END LOOP;
END $$;

-- ── 6. check_transition — the verification arc honors recorded grants ──────
-- v015's body (story-precondition early return, verified NOT GUC-authorised;
-- transition matrix check; story active-slice RAISE) is preserved VERBATIM;
-- the ONLY addition is the delegation branch in the v014 identity check. It
-- must be there: the trigger fires on verify_slice's UPDATE under the CALLER's
-- session_user (a granted worker is not is_orchestrator), so an unchanged v014
-- check would refuse the very path v017 exists to enable. The grant row is
-- slice-scoped, so the raw UPDATE path a granted worker could take stays
-- bounded to that one slice.
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
    -- v014 (with v017's delegation branch): the verification arc is closed to
    -- anyone without the tasks_verifier capability AND — for verified workers
    -- under recorded authority — bound to a live per-slice grant. session_user
    -- (never current_user) is the authenticated caller, which is the identity
    -- that still holds inside verify_slice's SECURITY DEFINER context.
    IF OLD.status = 'review' AND NEW.status = 'completed'
       AND NOT tasks.is_orchestrator(session_user)
       AND NOT tasks.verify_grant_active(OLD.id, session_user) THEN
        RAISE EXCEPTION
            'review -> completed requires orchestrator verification (caller %): '
            'the generic update path may not approve a slice — moses/esther run '
            'tasks.verify_slice (a recorded per-slice grant also admits the '
            'named verifier)', tasks.profile_of(session_user);
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

-- ── 7. verify_slice — capability OR recorded grant; grant is consumed ──────
CREATE OR REPLACE FUNCTION tasks.verify_slice(
  p_id UUID,
  p_approved boolean,
  p_note TEXT DEFAULT NULL
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = tasks, pg_temp AS $$
DECLARE
  v_updated int;
BEGIN
  -- Orchestrator (capability) or the verifier named in a live per-slice grant.
  -- Never trust a self-report: a worker's own verify requires a row written by
  -- the orchestrator (authorized_by + note = the audit trail).
  IF NOT (tasks.is_orchestrator(session_user)
          OR tasks.verify_grant_active(p_id, session_user)) THEN
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

-- WIDENED (the "grant" half of the fix): every profile role may CALL; the
-- inside gate still refuses everything unauthorized. This is what removes the
-- single-host dead-end — a worker host's own role can now reach the function,
-- and the recorded grant decides.
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
