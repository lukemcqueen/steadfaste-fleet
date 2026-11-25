# Shell traps that silently target the wrong thing

Two shapes where a shell script reads as correct, produces no error, and operates on the
wrong thing. Both are cheap to guard and expensive to debug, because the failure is a
MISSING symptom rather than a visible one.

---

## 1. A pipeline's exit status is the LAST command's

```bash
cmd | tail -5 || { fallback; }        # fallback NEVER runs
```

The pipeline's status is `tail`'s, and `tail` succeeded, so `cmd`'s failure is discarded
and the `||` branch is dead code. This is the FALLBACK shape of the pipefail trap — it
reads as error handling and silently is not.

Measured cost: a gate-blocked `git push` printed its refusal through `| tail`, the retry
branch that was supposed to follow never fired, and the block looked like a no-op until
the output was read line by line. The gate was working correctly the whole time.

**Rule.** Either set `set -o pipefail` at the top of the script (the pipeline then reports
the failing member), or branch on the PRODUCING command's own status:

```bash
out=$(cmd 2>&1); rc=$?
[ "$rc" = 0 ] || { fallback; }
printf '%s\n' "$out" | tail -5
```

When a fallback branch "did not trigger", suspect the pipe before suspecting the branch.
A `||` on a pipeline is a claim about the last command, not the first.

---

## 2. `$HOME` is ROOT's home under `sudo`

`sudo`'s default `env_reset` sets `HOME` to the TARGET user's home. A script that builds
paths from `$HOME` and is DOCUMENTED as `sudo bash <script>` therefore reads and writes the
wrong tree — silently, because `$HOME` expands to a perfectly valid path.

It surfaces as a confusing guard mismatch, not as a permission error:

    mount source is <caller-home>/..., expected <root-home>/...  — refusing to guess

That refusal is the GOOD outcome: a guard keyed on the resolved path turned a
wrong-directory operation into a clean error instead of a move from the wrong place.

**Rule.** Resolve the INVOKING identity, not the environment:

```bash
TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6 || true)"   # Linux
[ -n "$TARGET_HOME" ] || TARGET_HOME="$(dscl . -read "/Users/$TARGET_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}' || true)"  # macOS
[ -n "$TARGET_HOME" ] || TARGET_HOME="${HOME}"                                   # non-sudo fallback
OLD_DIR="${MYAPP_OLD_DIR:-$TARGET_HOME/.old-name/data}"
NEW_DIR="${MYAPP_NEW_DIR:-$TARGET_HOME/.new-name/data}"
```

Keep the explicit override variables as the escape hatch, and validate the resolved path
against LIVE state before acting on it rather than trusting the expansion:

```bash
docker inspect "$CONTAINER" --format '{{range .Mounts}}{{.Source}}{{end}}'
```

**Reproduce it WITHOUT root, so the fix is testable by anyone.** Any wrong `HOME` triggers
the same refusal:

```bash
HOME=/tmp/not-a-home bash ops/scripts/manage/<migration>.sh
```

That one line is the whole test: RED before the fix (refusal naming the wrong home),
GREEN after (correct plan printed). No sudo, no password, no live data touched.

**Never let a wrong `HOME` reach the destructive step** — resolve, validate against live
state, and only then act. A migration script whose paths come from `$HOME` under `sudo` is
one missing guard away from operating on the wrong directory.
