#!/bin/sh
# Roll back deploy/lab-trust/install.sh (docs/lab-trust.md). Root only.
# Stops only zabalack-lab-zabbix.service and removes only the paths install.sh created. It does not touch the
# host trust store, AppArmor, the lab account or any caller-selected directory. Nothing is removed unless systemd
# reports the unit inactive (or failed) with no process left in its control group; otherwise it exits 1 and the
# rollback is not complete.
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
if [ -n "$cgroup" ] && [ -e "/sys/fs/cgroup$cgroup/cgroup.procs" ] && [ -s "/sys/fs/cgroup$cgroup/cgroup.procs" ]; then
    echo "uninstall.sh: processes remain in $cgroup; nothing removed, rollback not complete" >&2; exit 1
fi
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
