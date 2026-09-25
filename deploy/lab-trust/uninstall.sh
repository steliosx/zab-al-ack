#!/bin/sh
# Roll back deploy/lab-trust/install.sh (docs/lab-trust.md). Root only.
# Stops only zabalack-lab-zabbix.service and removes only the paths install.sh created. It does not touch the
# host trust store, AppArmor, the lab account or any caller-selected directory. Nothing is removed unless systemd
# reports the unit inactive (or failed) and its control group is gone or reads as empty; otherwise it exits 1 and
# the rollback is not complete.
#
# Emptiness is read, never inferred from a file size (cgroupfs files report size 0 even when populated). For the
# group systemd reports and for the unit's fixed cgroup-v2 path, an existing group must have a readable
# cgroup.events saying "populated 0" (which covers every descendant group) and an empty, readable cgroup.procs.
# A group that no longer exists after the stop is accepted; one that exists but cannot be read is refused.
set -eu
UNIT=zabalack-lab-zabbix.service
[ "$(id -u)" = 0 ] || { echo "uninstall.sh: run as root" >&2; exit 2; }
/usr/bin/systemctl stop "$UNIT" 2>/dev/null || true
state=$(/usr/bin/systemctl show -p ActiveState --value "$UNIT") || { echo "uninstall.sh: cannot read the unit's state; nothing removed" >&2; exit 1; }
cgroup=$(/usr/bin/systemctl show -p ControlGroup --value "$UNIT") || { echo "uninstall.sh: cannot read the unit's control group; nothing removed" >&2; exit 1; }
case "$state" in
    inactive|failed) ;;
    *) echo "uninstall.sh: $UNIT is '$state' after stop; nothing removed, rollback not complete" >&2; exit 1 ;;
esac
group_empty() {  # a cgroup-v2 directory; 0 when gone or empty, otherwise names why and returns 1
    [ -e "$1" ] || [ -L "$1" ] || return 0
    events=$(cat "$1/cgroup.events" 2>/dev/null) || { echo "uninstall.sh: cannot read $1/cgroup.events; nothing removed, rollback not complete" >&2; return 1; }
    populated=$(printf '%s\n' "$events" | sed -n 's/^populated \([01]\)$/\1/p')
    case "$populated" in
        0) ;;
        1) echo "uninstall.sh: $1 is still populated (it or a descendant has processes); nothing removed, rollback not complete" >&2; return 1 ;;
        *) echo "uninstall.sh: $1/cgroup.events has no populated state; nothing removed, rollback not complete" >&2; return 1 ;;
    esac
    procs=$(cat "$1/cgroup.procs" 2>/dev/null) || { echo "uninstall.sh: cannot read $1/cgroup.procs; nothing removed, rollback not complete" >&2; return 1; }
    [ -z "$procs" ] || { echo "uninstall.sh: processes remain in $1; nothing removed, rollback not complete" >&2; return 1; }
}
if [ -n "$cgroup" ]; then group_empty "/sys/fs/cgroup$cgroup" || exit 1; fi
group_empty "/sys/fs/cgroup/system.slice/$UNIT" || exit 1
rm -f /etc/systemd/system/zabalack-lab-zabbix.service /etc/sudoers.d/zabalack-lab-zabbix /etc/tmpfiles.d/zabalack-lab-zabbix.conf
rm -rf /etc/zabalack-lab /run/zabalack-lab-zabbix
/usr/bin/systemctl daemon-reload
/usr/bin/systemctl reset-failed "$UNIT" 2>/dev/null || true
for path in /etc/zabalack-lab /etc/systemd/system/zabalack-lab-zabbix.service /etc/sudoers.d/zabalack-lab-zabbix \
            /etc/tmpfiles.d/zabalack-lab-zabbix.conf /run/zabalack-lab-zabbix; do
    if [ -e "$path" ] || [ -L "$path" ]; then echo "uninstall.sh: $path still present" >&2; exit 1; fi
done
[ "$(/usr/bin/systemctl show -p LoadState --value "$UNIT")" = not-found ] || { echo "uninstall.sh: $UNIT is still loaded" >&2; exit 1; }
echo "removed: lab-trust unit (inactive, empty control group), sudoers rule, tmpfiles rule, /etc/zabalack-lab, /run/zabalack-lab-zabbix"
