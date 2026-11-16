# Reading a credential/URL out of a config file, fail-closed

Recipe for a shell script that reads a named value out of a config or `.env` file
and must not hand it to anything until it is proven well-formed. Applies whenever
the value reaches `curl`, an API call, or an auth header.

## The trap that silently defeats the guard

`printf … | grep -q PATTERN` under `set -o pipefail` reports **"no match" for
exactly the values you must catch**: `grep -q` exits on the FIRST match, `printf`
then takes SIGPIPE, and `pipefail` makes the pipeline non-zero — so

```bash
if printf '%s' "$v" | grep -q '[[:space:]]'; then die "malformed"; fi   # ✗ passes the bad value
```
is False for a value containing whitespace. Use bash's own pattern match:

```bash
if [[ "$raw" == *[[:space:][:cntrl:]]* ]]; then die "$key contains whitespace"; fi
```

## The reader

```bash
conf_value() {
  local key="$1" raw count
  # Count FIRST: awk without `exit` prints EVERY match, so a duplicated key
  # silently becomes a multi-line value.
  count="$(awk -F= -v k="$key" '$1==k{n++} END{print n+0}' "$CONF")"
  [ "$count" -gt 0 ] || die "$key is not set in $CONF"
  [ "$count" -eq 1 ] || die "$key appears $count times in $CONF — refusing an ambiguous value"
  # Same predicate as the count pass. Two passes with DIFFERENT match semantics
  # can disagree about which line matched, which reads as a wrong refusal.
  raw="$(awk -F= -v k="$key" '$1==k{sub(/^[^=]*=/,""); print; exit}' "$CONF")"
  raw="${raw%$'\r'}"                     # CRLF-edited file
  raw="${raw#"${raw%%[![:space:]]*}"}"   # trim leading blanks
  raw="${raw%"${raw##*[![:space:]]}"}"   # trim trailing blanks
  [ -n "$raw" ] || die "$key is empty in $CONF"
  [[ "$raw" == *[[:space:][:cntrl:]]* ]] &&
    die "$key contains whitespace or a control character — malformed value in $CONF"
  printf '%s' "$raw"
}
```

Then validate each field's SHAPE before use — every refusal names the field:

```bash
case "$URL" in http://*|https://*) ;; *) die "the URL must start with http:// or https://";; esac
_auth="${URL#*://}"; _auth="${_auth%%/*}"          # authority only
[ -n "$_auth" ] || die "the URL has no host"
[[ "$_auth" == *@* ]] && die "the URL carries userinfo — put the credential in its own field"
[[ "$AUTH" == *:* ]] || die "the credential must be user:password"
[ -n "${AUTH%%:*}" ] || die "the credential has an empty user"
[ -n "${AUTH#*:}" ] || die "the credential has an empty password"
```

One `die()` helper (`echo "ERROR: $*" >&2; exit 1`) used by every refusal lets a
caller tell "the config is bad" from "the endpoint did not answer" without
parsing prose. REJECT embedded whitespace/control characters rather than silently
stripping them — that value is malformed, and quietly repairing it hides the
operator's mistake.

## Why each check exists (the observed symptoms)

- **Duplicated key** → multi-line value → `curl: (3) URL rejected: Malformed input
  to a URL function`, exit 3, and NOTHING names the offending key.
- **CRLF-edited file** → the CR rides into the value and is echoed on log lines
  (`== GET http://host<CR>/path ==`).
- **Padding** → `KEY= value` yields ` value`.
- **Unchecked shape** → a credential with no colon, or an empty user/password,
  cannot form a Basic credential; userinfo in the URL puts the credential in the
  string the script ECHOES and bypasses the dedicated auth field.

## Testing it

Drive the REAL script against a LOCAL stub server rather than asserting on error
text — that asserts the values actually parsed (the request path and the
`Authorization` header), so the test cannot go green on a script that
authenticates wrongly. A threaded `http.server` on port 0 that records the one
request is enough.

Give the script path an env override (`SCRIPT = Path(os.environ.get("X_SCRIPT", default))`)
so the SAME battery can be driven at the PRE-FIX revision:

```bash
git show <range-base>:<script> > /tmp/prefix/<same-basename>   # real basename: a suffix-less temp path loads as empty
X_SCRIPT=/tmp/prefix/<same-basename> pytest tests/<battery>.py -q   # must FAIL
pytest tests/<battery>.py -q                                        # must PASS
```

The RED run is what makes the fix's claims falsifiable; without it the battery is
a decoration.

## Case list worth carrying

Valid conf succeeds and authenticates (the regression guard) · CRLF conf is
TRIMMED and works · padding trimmed · duplicate key refused naming the field ·
missing field names the field · empty value refused · URL without scheme refused ·
URL with userinfo refused (and the credential never reaches stdout) · credential
without a colon refused · credential with an empty password refused · embedded
whitespace refused · both fields go through the validating reader · `bash -n` clean.

## Before hand-rolling, check for the canonical reader

`env-value.sh` reads ONE named variable out of the fleet env and is
name-injection-safe. It takes the FIRST match and neither strips a CR nor
validates the value's shape, so it is NOT a substitute when the finding is
duplicate/CRLF/shape handling — but it is the right tool when you only need one
secret out of a `.env` and nothing more.
