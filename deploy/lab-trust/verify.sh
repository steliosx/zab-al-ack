#!/bin/sh
# Read-only audit of an installed lab-trust unit (docs/lab-trust.md): owners, modes, exact entries, contents and the CA's
# exact constraints. Root only: it reads the 0440 sudoers file and the CA key. Run it from the same immutable,
# root-owned snapshot install.sh ran from. It prints each installed file's SHA-256.
# Exit 0 when everything matches what install.sh creates; otherwise it names each difference and exits 1 (2: cannot audit).
set -u
[ "$(id -u)" = 0 ] || { echo "verify.sh: run as root (the sudoers file and the CA key are not readable otherwise)" >&2; exit 2; }
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
fail=0
expect() {  # path owner:group mode type
    actual=$(stat -c '%U:%G %a %F' "$1" 2>/dev/null) || { echo "missing: $1"; fail=1; return; }
    [ "$actual" = "$2 $3 $4" ] || { echo "differs: $1 is '$actual', expected '$2 $3 $4'"; fail=1; }
}
exactly() {  # directory, then its expected entries
    directory=$1; shift
    entries=$(ls -A "$directory" 2>/dev/null | LC_ALL=C sort | tr '\n' ' ')
    wanted=$(printf '%s\n' "$@" | LC_ALL=C sort | tr '\n' ' ')
    [ "$entries" = "$wanted" ] || { echo "differs: $directory holds '$entries', expected '$wanted'"; fail=1; }
}
sh "$SOURCE/snapshot-check.sh" "$SOURCE" || exit 2
hash=$(/usr/bin/openssl x509 -noout -subject_hash -in /etc/zabalack-lab/trust/lab-ca.pem 2>/dev/null) || hash=unreadable
expect /etc/zabalack-lab root:root 755 directory
expect /etc/zabalack-lab/trust root:root 755 directory
expect /etc/zabalack-lab/trust/ca-certificates.crt root:root 644 'regular file'
expect /etc/zabalack-lab/trust/lab-ca.pem root:root 644 'regular file'
expect "/etc/zabalack-lab/trust/$hash.0" root:root 777 'symbolic link'
expect /etc/zabalack-lab/ca root:zabalack-runner 750 directory
expect /etc/zabalack-lab/ca/lab-ca.key root:zabalack-runner 640 'regular file'
expect /etc/zabalack-lab/ca/lab-ca.pem root:root 644 'regular file'
expect /etc/systemd/system/zabalack-lab-zabbix.service root:root 644 'regular file'
expect /etc/sudoers.d/zabalack-lab-zabbix root:root 440 'regular file'
expect /etc/tmpfiles.d/zabalack-lab-zabbix.conf root:root 644 'regular file'
expect /run/zabalack-lab-zabbix zabalack-runner:zabalack-runner 700 directory
exactly /etc/zabalack-lab ca trust
for pair in "zabalack-lab-zabbix.service /etc/systemd/system/zabalack-lab-zabbix.service" \
            "zabalack-lab-zabbix.sudoers /etc/sudoers.d/zabalack-lab-zabbix" \
            "zabalack-lab-zabbix.tmpfiles /etc/tmpfiles.d/zabalack-lab-zabbix.conf"; do
    set -- $pair
    cmp -s "$SOURCE/$1" "$2" 2>/dev/null || { echo "differs from the reviewed source: $2"; fail=1; }
done
sh "$SOURCE/check-ca.sh" /etc/zabalack-lab/trust /etc/zabalack-lab/ca || fail=1
echo "installed sha256:"
for path in /etc/zabalack-lab/trust/ca-certificates.crt /etc/zabalack-lab/trust/lab-ca.pem /etc/zabalack-lab/ca/lab-ca.pem \
            /etc/systemd/system/zabalack-lab-zabbix.service /etc/sudoers.d/zabalack-lab-zabbix /etc/tmpfiles.d/zabalack-lab-zabbix.conf; do
    sha256sum "$path" 2>/dev/null || { echo "unreadable: $path"; fail=1; }
done
[ "$fail" = 0 ] && echo "lab-trust installation matches the reviewed files"
exit "$fail"
