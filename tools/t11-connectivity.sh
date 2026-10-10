#!/bin/sh
# Unauthenticated, bounded checks. No HTTP requests, credentials or retries.
set -u
printf 'observed_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf 'environment='; uname -srm
openssl version
set --
if [ -r "${T11_CA_FILE:-/diagnostic-ca.pem}" ]; then
  set -- -CAfile "${T11_CA_FILE:-/diagnostic-ca.pem}"
  printf 'explicit_public_ca_bundle=true\n'
else
  printf 'explicit_public_ca_bundle=false\n'
fi
for host in freya-bank-mock.freyavoice.workers.dev www.cloudflare.com; do
  printf '\nhost=%s dns_ipv4 deadline_seconds=5\n' "$host"
  timeout 5 getent ahostsv4 "$host"
  printf 'dns_exit=%s\n' "$?"
  printf 'host=%s tls_ipv4 port=443 deadline_seconds=8 verify_hostname=true\n' "$host"
  timeout 8 openssl s_client -4 -connect "$host:443" -servername "$host" \
    -verify_hostname "$host" -verify_return_error -brief -state "$@" </dev/null
  printf 'tls_exit=%s\n' "$?"
done
