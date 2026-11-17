#!/usr/bin/env bash
# Agent Bus poll — inbox_titus
# Reads credentials from hermes-inbox.conf; never embeds secrets.
# Usage: ./poll-bus-inbox_titus.sh
# Correct behavior: exits 0 on ANY successful authenticated queue read (2xx),
# regardless of message count. Fails (non-zero) on connection error, non-2xx,
# missing/unreadable/malformed config, missing or malformed URL/AUTH, or
# malformed JSON payload.
# Prints the message count so callers/crons can branch on it.
#
# Config handling is strict on purpose (security finding, 2026-11-16). The old
# parser ran `awk` and used whatever it printed, so:
#   * a DUPLICATED key produced a MULTI-LINE value,
#   * a CRLF-edited file rode its CR into the URL (the CR was even echoed on the
#     log line), and
#   * surrounding padding was never trimmed,
# all of which reached curl as a malformed URL or credential — the observed
# symptom being "curl: (3) URL rejected: Malformed input to a URL function".
# Every value is now validated BEFORE any network call, and each refusal names
# the field at fault so the operator can repair the conf directly.
set -euo pipefail

CONF="${FLEET_INBOX_CONF:-$HOME/.steadfaste-fleet/hermes-inbox.conf}"
[ -r "$CONF" ] || { echo "ERROR: cannot read $CONF" >&2; exit 1; }

# die MESSAGE — a single refusal path, always non-zero and always on stderr, so
# a caller/cron can tell "the config is bad" apart from "the queue did not
# answer" without parsing prose.
die() { echo "ERROR: $*" >&2; exit 1; }

# conf_value KEY — print KEY's value, or die naming the defect.
#
# Exactly ONE matching line is required: awk used to print every match, so a
# duplicated key silently became a multi-line value. One trailing CR is dropped
# (a CRLF-edited conf must still work), surrounding blanks are trimmed, and an
# empty value, embedded whitespace, or a control character is refused rather
# than handed to curl.
conf_value() {
  local key="$1" raw count
  count="$(awk -F= -v k="$key" '$1==k{n++} END{print n+0}' "$CONF")"
  [ "$count" -gt 0 ] || die "$key is not set in $CONF"
  [ "$count" -eq 1 ] || die "$key appears $count times in $CONF — refusing an ambiguous value"
  raw="$(awk -F= -v k="$key" '$1==k{sub(/^[^=]*=/,""); print; exit}' "$CONF")"
  raw="${raw%$'\r'}"                          # CRLF-edited conf
  raw="${raw#"${raw%%[![:space:]]*}"}"        # trim leading blanks
  raw="${raw%"${raw##*[![:space:]]}"}"        # trim trailing blanks
  [ -n "$raw" ] || die "$key is empty in $CONF"
  # Bash's own pattern match rather than `grep -q` in a pipeline: grep -q exits
  # on the first match, printf takes SIGPIPE, and `set -o pipefail` would then
  # report "no match" for exactly the values this must catch.
  if [[ "$raw" == *[[:space:][:cntrl:]]* ]]; then
    die "$key contains whitespace or a control character — malformed value in $CONF"
  fi
  printf '%s' "$raw"
}

URL="$(conf_value FLEET_BUS_URL)" || exit 1
AUTH="$(conf_value FLEET_BASIC_AUTH)" || exit 1

case "$URL" in
  http://*|https://*) ;;
  *) die "FLEET_BUS_URL must start with http:// or https:// in $CONF" ;;
esac

# A credential inside the URL would be echoed by the log lines below and would
# bypass FLEET_BASIC_AUTH entirely, so require it to live in its own field.
_url_authority="${URL#*://}"
_url_authority="${_url_authority%%/*}"
[ -n "$_url_authority" ] || die "FLEET_BUS_URL has no host in $CONF"
if [[ "$_url_authority" == *@* ]]; then
  die "FLEET_BUS_URL carries userinfo — put the credential in FLEET_BASIC_AUTH instead"
fi
unset _url_authority

# curl -u requires user:password; anything else cannot form a Basic credential.
[[ "$AUTH" == *:* ]] || die "FLEET_BASIC_AUTH must be user:password in $CONF"
[ -n "${AUTH%%:*}" ] || die "FLEET_BASIC_AUTH has an empty user in $CONF"
[ -n "${AUTH#*:}" ] || die "FLEET_BASIC_AUTH has an empty password in $CONF"

echo "RUN $(date -u +'%Y-%m-%d %H:%M:%S') UTC"
echo "== GET $URL/api/pgmq/peek/inbox_titus =="
# -sS: show curl errors; --fail-with-body: non-2xx -> nonzero exit (real failure).
BODY="$(curl -sS --fail-with-body -m 25 -u "$AUTH" "$URL/api/pgmq/peek/inbox_titus")"
printf '%s\n' "$BODY"
# Assert the payload has a well-formed message-count (does NOT require it be 0).
python3 -c 'import sys,json; d=json.load(sys.stdin); assert "count" in d and isinstance(d.get("messages"), list), "unexpected payload shape"; print("queue_read_ok count=%d (any 2xx read is success)" % d["count"])' <<<"$BODY"
echo "EXIT:0"
