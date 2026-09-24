#!/bin/sh
# Roll back deploy/lab-trust/install.sh (docs/lab-trust.md). Root only.
# Stops only zabalack-lab-zabbix.service and removes only the paths install.sh created. It does not touch the
# host trust store, AppArmor, the lab account or any caller-selected directory.
set -eu
[ "$(id -u)" = 0 ] || { echo "uninstall.sh: run as root" >&2; exit 2; }
/usr/bin/systemctl stop zabalack-lab-zabbix.service 2>/dev/null || true
rm -f /etc/systemd/system/zabalack-lab-zabbix.service /etc/sudoers.d/zabalack-lab-zabbix /etc/tmpfiles.d/zabalack-lab-zabbix.conf
rm -rf /etc/zabalack-lab /run/zabalack-lab-zabbix
/usr/bin/systemctl daemon-reload
/usr/bin/systemctl reset-failed zabalack-lab-zabbix.service 2>/dev/null || true
for path in /etc/zabalack-lab /etc/systemd/system/zabalack-lab-zabbix.service /etc/sudoers.d/zabalack-lab-zabbix \
            /etc/tmpfiles.d/zabalack-lab-zabbix.conf /run/zabalack-lab-zabbix; do
    if [ -e "$path" ] || [ -L "$path" ]; then echo "uninstall.sh: $path still present" >&2; exit 1; fi
done
echo "removed: lab-trust unit, sudoers rule, tmpfiles rule, /etc/zabalack-lab, /run/zabalack-lab-zabbix"
