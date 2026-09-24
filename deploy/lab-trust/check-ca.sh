#!/bin/sh
# Exact content check of the lab CA and the unit's trust view (docs/lab-trust.md); read-only, no privilege of its own.
# usage: check-ca.sh TRUST_DIR CA_DIR   (installed: /etc/zabalack-lab/trust /etc/zabalack-lab/ca; verify.sh runs it as root)
# Exit 0 only when:
#   - TRUST_DIR holds exactly ca-certificates.crt, lab-ca.pem and <subject-hash>.0, a link to lab-ca.pem;
#   - CA_DIR holds exactly lab-ca.key and lab-ca.pem;
#   - all three certificate files are the same single certificate, self-signed, matching lab-ca.key;
#   - its extensions are exactly subject/authority key identifiers plus the three below, with these exact values:
#     CA:TRUE with path length 0, keyCertSign and cRLSign only, and names permitted only for DNS:runner and IP:127.0.0.1/32.
# Otherwise it names each difference and exits 1.
set -u
[ "$#" = 2 ] || { echo "usage: check-ca.sh TRUST_DIR CA_DIR" >&2; exit 2; }
TRUST=$1
CA=$2
OPENSSL=/usr/bin/openssl
fail=0
differs() { echo "$1"; fail=1; }
pem="$TRUST/lab-ca.pem"
hash=$("$OPENSSL" x509 -noout -subject_hash -in "$pem" 2>/dev/null) || { differs "unreadable lab CA certificate: $pem"; hash=unreadable; }
entries=$(ls -A "$TRUST" 2>/dev/null | LC_ALL=C sort | tr '\n' ' ')
expected=$(printf '%s\n' ca-certificates.crt lab-ca.pem "$hash.0" | LC_ALL=C sort | tr '\n' ' ')
[ "$entries" = "$expected" ] || differs "trust entries are '$entries', expected '$expected'"
[ -L "$TRUST/$hash.0" ] && [ "$(readlink "$TRUST/$hash.0")" = lab-ca.pem ] || differs "$TRUST/$hash.0 is not a link to lab-ca.pem"
entries=$(ls -A "$CA" 2>/dev/null | LC_ALL=C sort | tr '\n' ' ')
[ "$entries" = "lab-ca.key lab-ca.pem " ] || differs "CA entries are '$entries', expected 'lab-ca.key lab-ca.pem '"
for copy in "$TRUST/ca-certificates.crt" "$CA/lab-ca.pem"; do
    cmp -s "$pem" "$copy" || differs "$copy is not the same certificate as $pem"
done
[ "$(grep -c -- '-----BEGIN' "$pem" 2>/dev/null)" = 1 ] || differs "$pem is not exactly one PEM object"
"$OPENSSL" verify -no-CApath -no-CAstore -CAfile "$pem" "$pem" >/dev/null 2>&1 || differs "the lab CA is not a valid self-signed CA"
cert_key=$("$OPENSSL" x509 -noout -pubkey -in "$pem" 2>/dev/null)
key=$("$OPENSSL" pkey -pubout -in "$CA/lab-ca.key" 2>/dev/null)
[ -n "$key" ] && [ "$key" = "$cert_key" ] || differs "$CA/lab-ca.key is not the lab CA's key"
constraints=$("$OPENSSL" x509 -noout -ext basicConstraints,keyUsage,nameConstraints -in "$pem" 2>/dev/null)
required='X509v3 Basic Constraints: critical
    CA:TRUE, pathlen:0
X509v3 Key Usage: critical
    Certificate Sign, CRL Sign
X509v3 Name Constraints: critical
    Permitted:
      DNS:runner
      IP:127.0.0.1/255.255.255.255'
[ "$constraints" = "$required" ] || differs "the lab CA's constraints differ from the reviewed ones:
$constraints"
extensions=$("$OPENSSL" x509 -noout -text -in "$pem" 2>/dev/null \
    | sed -n '/^        X509v3 extensions:$/,/^    Signature Algorithm/s/^            \([^ ].*\):\( critical\)\{0,1\} *$/\1/p' | LC_ALL=C sort | tr '\n' ',')
[ "$extensions" = "X509v3 Authority Key Identifier,X509v3 Basic Constraints,X509v3 Key Usage,X509v3 Name Constraints,X509v3 Subject Key Identifier," ] \
    || differs "the lab CA carries other extensions: $extensions"
[ "$fail" = 0 ] && echo "lab CA and trust view match the reviewed constraints"
exit "$fail"
