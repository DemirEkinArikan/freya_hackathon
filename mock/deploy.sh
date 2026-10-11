#!/usr/bin/env bash
# Deploy a mock built by build_mock.py to Cloudflare Workers, check it answers, print its URL (the only line on stdout).
#   mock/deploy.sh mock/build/final
# Credentials: CLOUDFLARE_API_TOKEN (Workers edit permission, plus CLOUDFLARE_ACCOUNT_ID when the token sees several
# accounts) in the environment, or ~/.freya-cloudflare-token (a bare token, or KEY=VALUE lines), or `npx wrangler login`.
set -euo pipefail
DIR=${1:?usage: mock/deploy.sh <build_dir>}
TOKEN_FILE="$HOME/.freya-cloudflare-token"
if [ -z "${CLOUDFLARE_API_TOKEN:-}" ] && [ -s "$TOKEN_FILE" ]; then
  if grep -q '=' "$TOKEN_FILE"; then
    value() { sed -n "s/^[[:space:]]*\(export[[:space:]]*\)\{0,1\}$1[[:space:]]*=[[:space:]]*[\"']\{0,1\}\([^\"']*\)[\"']\{0,1\}[[:space:]]*$/\2/p" "$TOKEN_FILE" | head -1; }
    export CLOUDFLARE_API_TOKEN="$(value CLOUDFLARE_API_TOKEN)"
    [ -n "$(value CLOUDFLARE_ACCOUNT_ID)" ] && export CLOUDFLARE_ACCOUNT_ID="$(value CLOUDFLARE_ACCOUNT_ID)"
  else
    export CLOUDFLARE_API_TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")"
  fi
fi
cd "$DIR"
export WRANGLER_SEND_METRICS=false
LOG=$(mktemp)
if ! npx --yes wrangler@4.141.0 deploy >"$LOG" 2>&1; then
  cat "$LOG" >&2
  echo "deploy failed. Without credentials: needs npx wrangler login or a Cloudflare API token with Workers edit permission." >&2
  exit 1
fi
cat "$LOG" >&2
URL=$(grep -Eo 'https://[A-Za-z0-9.-]+\.workers\.dev' "$LOG" | head -1)
[ -n "$URL" ] || { echo "deployed, but wrangler printed no workers.dev URL (register a workers.dev subdomain in the Cloudflare dashboard)" >&2; exit 1; }
for i in 1 2 3 4 5 6; do
  if curl -fsS --max-time 10 "$URL/_mock/health" >/dev/null; then echo "health ok: $URL/_mock/health" >&2; echo "$URL"; exit 0; fi
  sleep 5
done
echo "deployed to $URL but /_mock/health does not answer yet" >&2
echo "$URL"
exit 2
