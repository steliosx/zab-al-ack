#!/bin/sh
# The installer and the audit read their sources only from an immutable, owner-approved snapshot (docs/lab-trust.md):
# the directory and every ancestor owned by root and writable by nobody else, each source file root-owned and not
# group- or other-writable, and every file matching the snapshot's SHA256SUMS. usage: snapshot-check.sh DIR
set -u
[ "$#" = 1 ] || { echo "usage: snapshot-check.sh DIR" >&2; exit 2; }
SNAPSHOT=$1
directory=$SNAPSHOT
while :; do
    info=$(stat -L -c '%u %a' "$directory" 2>/dev/null) || { echo "snapshot: cannot stat $directory" >&2; exit 1; }
    set -- $info
    [ "$1" = 0 ] || { echo "snapshot: $directory is not owned by root" >&2; exit 1; }
    case "$2" in *[2367]?|*?[2367]) [ -k "$directory" ] && [ "$directory" != "$SNAPSHOT" ] || { echo "snapshot: $directory is writable by others" >&2; exit 1; } ;; esac
    [ "$directory" = / ] && break
    directory=$(dirname -- "$directory")
done
for name in SHA256SUMS install.sh uninstall.sh verify.sh check-ca.sh snapshot-check.sh zabalack-lab-zabbix.service \
            zabalack-lab-zabbix.sudoers zabalack-lab-zabbix.tmpfiles; do
    path="$SNAPSHOT/$name"
    [ -f "$path" ] && [ ! -L "$path" ] || { echo "snapshot: $path is not a regular file" >&2; exit 1; }
    set -- $(stat -c '%u %a' "$path")
    [ "$1" = 0 ] || { echo "snapshot: $path is not owned by root" >&2; exit 1; }
    case "$2" in *[2367]?|*?[2367]) echo "snapshot: $path is writable by others" >&2; exit 1 ;; esac
done
(cd "$SNAPSHOT" && sha256sum --strict --quiet -c SHA256SUMS) >&2 || { echo "snapshot: files differ from SHA256SUMS" >&2; exit 1; }
[ "$(wc -l < "$SNAPSHOT/SHA256SUMS")" = 8 ] || { echo "snapshot: SHA256SUMS does not list exactly the eight sources" >&2; exit 1; }
