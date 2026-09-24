#!/bin/sh
# Read-only check of an installed lab-trust unit (docs/lab-trust.md): owners, modes, contents, CA constraints.
# Exit 0 when everything matches what install.sh creates; otherwise it names each difference and exits 1.
set -u
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
fail=0
expect() {  # path owner:group mode type
    actual=$(stat -c '%U:%G %a %F' "$1" 2>/dev/null) || { echo "missing: $1"; fail=1; return; }
    [ "$actual" = "$2 $3 $4" ] || { echo "differs: $1 is '$actual', expected '$2 $3 $4'"; fail=1; }
}
expect /etc/zabalack-lab root:root 755 directory
expect /etc/zabalack-lab/trust root:root 755 directory
expect /etc/zabalack-lab/trust/ca-certificates.crt root:root 644 'regular file'
expect /etc/zabalack-lab/trust/lab-ca.pem root:root 644 'regular file'
expect /etc/zabalack-lab/ca root:zabalack-runner 750 directory
expect /etc/zabalack-lab/ca/lab-ca.key root:zabalack-runner 640 'regular file'
expect /etc/zabalack-lab/ca/lab-ca.pem root:root 644 'regular file'
expect /etc/systemd/system/zabalack-lab-zabbix.service root:root 644 'regular file'
expect /etc/sudoers.d/zabalack-lab-zabbix root:root 440 'regular file'
expect /etc/tmpfiles.d/zabalack-lab-zabbix.conf root:root 644 'regular file'
expect /run/zabalack-lab-zabbix zabalack-runner:zabalack-runner 700 directory
for pair in "zabalack-lab-zabbix.service /etc/systemd/system/zabalack-lab-zabbix.service" \
            "zabalack-lab-zabbix.sudoers /etc/sudoers.d/zabalack-lab-zabbix" \
            "zabalack-lab-zabbix.tmpfiles /etc/tmpfiles.d/zabalack-lab-zabbix.conf"; do
    set -- $pair
    cmp -s "$SOURCE/$1" "$2" 2>/dev/null || { echo "differs from the reviewed source: $2"; fail=1; }
done
cmp -s /etc/zabalack-lab/trust/ca-certificates.crt /etc/zabalack-lab/ca/lab-ca.pem || { echo "trust file is not the lab CA"; fail=1; }
openssl x509 -noout -ext nameConstraints,basicConstraints -in /etc/zabalack-lab/trust/ca-certificates.crt 2>/dev/null \
    | tr -d ' ' | grep -q 'Permitted:' || { echo "lab CA lacks name constraints"; fail=1; }
for extra in /etc/zabalack-lab/trust/*; do
    case "$(basename "$extra")" in ca-certificates.crt|lab-ca.pem|*.0) ;; *) echo "unexpected trust file: $extra"; fail=1 ;; esac
done
[ "$fail" = 0 ] && echo "lab-trust installation matches the reviewed files"
exit "$fail"
