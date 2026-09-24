#!/bin/sh
# Install the Zabalack lab-trust unit on the isolated lab host (docs/lab-trust.md). Root only; owner-authorized only.
# Creates exactly these paths, and refuses if any already exists:
#   /etc/zabalack-lab/                       root:root              0755
#   /etc/zabalack-lab/trust/                 root:root              0755  public: the lab CA certificate only
#     ca-certificates.crt, lab-ca.pem        root:root              0644  (the CA file and directory libcurl uses)
#     <subject-hash>.0 -> lab-ca.pem         root:root                    (CA directory lookup)
#   /etc/zabalack-lab/ca/                    root:zabalack-runner   0750
#     lab-ca.key                             root:zabalack-runner   0640  signs each run's lab certificate; never exported
#     lab-ca.pem                             root:root              0644
#   /etc/systemd/system/zabalack-lab-zabbix.service   root:root  0644
#   /etc/sudoers.d/zabalack-lab-zabbix                root:root  0440  (checked with visudo -cf before it is placed)
#   /etc/tmpfiles.d/zabalack-lab-zabbix.conf          root:root  0644  -> /run/zabalack-lab-zabbix 0700 zabalack-runner
# The lab CA is name-constrained to DNS:runner and IP:127.0.0.1, path length 0. The host trust store, AppArmor,
# the lab account's groups and every other file are left unchanged. Rollback: deploy/lab-trust/uninstall.sh.
#
# usage: install.sh APPROVED_SHA256SUMS_DIGEST
# It installs only from an immutable, owner-approved snapshot: this directory must pass snapshot-check.sh (root-owned
# path, no group/other write, every source matching SHA256SUMS) and SHA256SUMS itself must have the SHA-256 the owner
# approved, given as the argument. A runner-writable checkout is refused.
set -eu
umask 022
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
ACCOUNT=zabalack-runner
[ "$(id -u)" = 0 ] || { echo "install.sh: run as root" >&2; exit 2; }
[ "$#" = 1 ] || { echo "usage: install.sh APPROVED_SHA256SUMS_DIGEST" >&2; exit 2; }
sh "$SOURCE/snapshot-check.sh" "$SOURCE" || { echo "install.sh: refusing, $SOURCE is not an immutable snapshot" >&2; exit 3; }
manifest=$(sha256sum "$SOURCE/SHA256SUMS" | cut -d' ' -f1)
[ "$manifest" = "$1" ] || { echo "install.sh: refusing, SHA256SUMS is $manifest, not the approved $1" >&2; exit 3; }
id "$ACCOUNT" >/dev/null 2>&1 || { echo "install.sh: account $ACCOUNT missing" >&2; exit 2; }
for tool in /usr/bin/setpriv /usr/sbin/zabbix_server /usr/bin/openssl /usr/sbin/visudo /usr/bin/systemctl /usr/bin/systemd-tmpfiles; do
    [ -x "$tool" ] || { echo "install.sh: $tool missing" >&2; exit 2; }
done
for path in /etc/zabalack-lab /etc/systemd/system/zabalack-lab-zabbix.service /etc/sudoers.d/zabalack-lab-zabbix \
            /etc/tmpfiles.d/zabalack-lab-zabbix.conf /run/zabalack-lab-zabbix; do
    if [ -e "$path" ] || [ -L "$path" ]; then echo "install.sh: refusing, $path exists (uninstall.sh first)" >&2; exit 3; fi
done

# The CA, generated in a root-only staging directory and moved into place.
stage=$(mktemp -d /root/zabalack-lab-trust.XXXXXX)
trap 'rm -rf "$stage"' EXIT
/usr/bin/openssl req -x509 -newkey rsa:3072 -nodes -days 825 -subj "/CN=Zabalack isolated lab CA" \
    -keyout "$stage/lab-ca.key" -out "$stage/lab-ca.pem" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "nameConstraints=critical,permitted;DNS:runner,permitted;IP:127.0.0.1/255.255.255.255" 2>/dev/null
install -d -o root -g root -m 0755 /etc/zabalack-lab /etc/zabalack-lab/trust
install -d -o root -g "$ACCOUNT" -m 0750 /etc/zabalack-lab/ca
install -o root -g "$ACCOUNT" -m 0640 "$stage/lab-ca.key" /etc/zabalack-lab/ca/lab-ca.key
install -o root -g root -m 0644 "$stage/lab-ca.pem" /etc/zabalack-lab/ca/lab-ca.pem
install -o root -g root -m 0644 "$stage/lab-ca.pem" /etc/zabalack-lab/trust/lab-ca.pem
install -o root -g root -m 0644 "$stage/lab-ca.pem" /etc/zabalack-lab/trust/ca-certificates.crt
ln -s lab-ca.pem "/etc/zabalack-lab/trust/$(/usr/bin/openssl x509 -noout -subject_hash -in "$stage/lab-ca.pem").0"

# The sudoers file is syntax-checked before it is placed.
install -o root -g root -m 0440 "$SOURCE/zabalack-lab-zabbix.sudoers" "$stage/sudoers"
/usr/sbin/visudo -cf "$stage/sudoers" >/dev/null
install -o root -g root -m 0440 "$stage/sudoers" /etc/sudoers.d/zabalack-lab-zabbix
install -o root -g root -m 0644 "$SOURCE/zabalack-lab-zabbix.service" /etc/systemd/system/zabalack-lab-zabbix.service
install -o root -g root -m 0644 "$SOURCE/zabalack-lab-zabbix.tmpfiles" /etc/tmpfiles.d/zabalack-lab-zabbix.conf
/usr/bin/systemctl daemon-reload
/usr/bin/systemd-tmpfiles --create /etc/tmpfiles.d/zabalack-lab-zabbix.conf

echo "installed:"
for path in /etc/zabalack-lab /etc/zabalack-lab/trust /etc/zabalack-lab/trust/* /etc/zabalack-lab/ca /etc/zabalack-lab/ca/lab-ca.pem \
            /etc/zabalack-lab/ca/lab-ca.key /etc/systemd/system/zabalack-lab-zabbix.service /etc/sudoers.d/zabalack-lab-zabbix \
            /etc/tmpfiles.d/zabalack-lab-zabbix.conf /run/zabalack-lab-zabbix; do
    stat -c '%U:%G %a %F %n' "$path"
done
echo "lab CA certificate sha256: $(sha256sum /etc/zabalack-lab/trust/ca-certificates.crt | cut -d' ' -f1)"
echo "installed from $SOURCE, SHA256SUMS $manifest"
