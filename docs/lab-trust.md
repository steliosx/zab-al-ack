# Lab trust: the lab's zabbix_server trusts the lab CA, and nothing else changes

With S2 (`docs/webhook-transport.md`) the webhook verifies TLS. The deployment lab's own webhook delivery goes to
`https://runner:58081/alert`, so the lab's `zabbix_server` must trust the lab certificate. Two constraints apply:
- Zabbix 7.0.31's HttpRequest has no per-request CA option; it uses libcurl's compiled defaults,
  `/etc/ssl/certs/ca-certificates.crt` and `/etc/ssl/certs`.
- On the lab host, AppArmor refuses unprivileged user namespaces, so the lab account cannot give its daemon a
  private store.

This page is the candidate from `LAB_TRUST_CONTRACT.md`: a fixed, root-owned systemd unit. **It is not installed.**
Installing it is a host change that needs the owner's authorization.

## Design

- **A fixed lab CA**, created by `install.sh`:
  - name-constrained to `DNS:runner` and `IP:127.0.0.1`, with path length 0;
  - its certificate is public; its key is readable only by root and the lab group;
  - the lab signs each run's `runner` certificate with it.
- **A fixed unit** (`zabalack-lab-zabbix.service`):
  1. systemd creates a private mount namespace;
  2. it bind-mounts the root-owned `/etc/zabalack-lab/trust`, read-only, over `/etc/ssl/certs`;
  3. it runs `/usr/bin/setpriv`, which clears supplementary groups, sets the lab account's GID and UID, empties
     the inheritable, ambient and bounding capabilities, sets no_new_privs and resets the environment;
  4. it then executes `/usr/sbin/zabbix_server --foreground --config /run/zabalack-lab-zabbix/zabbix_server.conf`.
- **Nothing the lab account controls is used as root:**
  - no argument, environment, mount source or mount target comes from a caller;
  - the configuration file is runner-owned but is read by the daemon only after the drop;
  - no repository code runs as root.
- **Why a fixed CA rather than one per run:** a per-run CA would have root mount or parse material the runner
  supplies. The fixed CA's key stays on the lab host, and only this unit's namespace trusts that CA.
- **Invocation:** a sudoers rule lets the lab account run exactly `systemctl start zabalack-lab-zabbix.service`
  and `systemctl stop zabalack-lab-zabbix.service`. There are no wildcards and no other commands.
- **Lab integration:**
  - When the unit is installed, `lab/zabbix_native_lab.py` starts `zabbix_server` only through it. First it checks
    that `/run/zabalack-lab-zabbix` is the lab account's own 0700 directory, not a link. It refuses any other
    requested executable, verifies the daemon by the unit's argv, and also stops the unit.
  - `tools/run_deployment_e2e.py` signs the run's `runner`/127.0.0.1 certificate with the lab CA. `server.crt`
    holds the leaf followed by the CA, so the lab's own clients are unchanged.
  - Without the unit, the deployment lab's layout check refuses the cycle before any service starts
    (`layout_not_ready`, naming this step), and the local runner refuses with `lab_trust_not_provisioned`.
- **Existing privilege context:** the lab account is a member of the `docker` group. The unit's daemon does not
  keep that membership (supplementary groups are cleared), but the account keeps it. This page makes no
  least-privilege claim about the account, and does not change its membership.

## Installation diff (what `install.sh` creates; it refuses if any path exists)

| Path | Owner | Mode | Content |
|---|---|---|---|
| `/etc/zabalack-lab/` | root:root | 0755 | directory |
| `/etc/zabalack-lab/trust/` | root:root | 0755 | public trust view of the daemon |
| `…/trust/ca-certificates.crt`, `…/trust/lab-ca.pem` | root:root | 0644 | the lab CA certificate |
| `…/trust/<subject-hash>.0` | root:root | link | → `lab-ca.pem` (CA directory lookup) |
| `/etc/zabalack-lab/ca/` | root:zabalack-runner | 0750 | directory |
| `…/ca/lab-ca.key` | root:zabalack-runner | 0640 | the CA key; never in evidence |
| `…/ca/lab-ca.pem` | root:root | 0644 | the CA certificate |
| `/etc/systemd/system/zabalack-lab-zabbix.service` | root:root | 0644 | `deploy/lab-trust/zabalack-lab-zabbix.service` |
| `/etc/sudoers.d/zabalack-lab-zabbix` | root:root | 0440 | `deploy/lab-trust/zabalack-lab-zabbix.sudoers`, `visudo -cf` first |
| `/etc/tmpfiles.d/zabalack-lab-zabbix.conf` | root:root | 0644 | `/run/zabalack-lab-zabbix` 0700 zabalack-runner |

Dependencies are all already on the lab host: util-linux `setpriv` 2.39.3, systemd 255, the packaged `zabbix_server`
7.0.31, OpenSSL 3.0.13 and `visudo`.

Not changed:
- `/etc/ssl/certs` and `update-ca-certificates`;
- `/etc/hosts`;
- AppArmor;
- the lab account and its groups;
- the Actions runner, its hooks and its labels.

After installing, `deploy/lab-trust/verify.sh` checks owners, modes, contents and the CA's constraints, read-only.

**Rollback:** `deploy/lab-trust/uninstall.sh` stops only this unit, removes only the paths in the table plus
`/run/zabalack-lab-zabbix`, and reloads systemd. It never touches the global trust store, AppArmor or any
caller-selected directory.

## Pre-install validation (done; no persistent change)

`lab/lab_trust_validation.py --mode pre-install` ran as root on `zabalack-omarchy-x64` at `244cefc`:
- **Setup:** the shipped unit, copied to `/run/systemd/system` with one difference, its trust source (a root-owned
  `/run` directory holding a CA made with `install.sh`'s own command). The lab's own `zabbix_server` was replaced
  by the unit's. `systemd-analyze verify` of the shipped unit is clean.
- **Result:** 8 of 8 cases passed, and the host was unchanged. The trust bundle, the certificate-directory
  listing, `/etc/hosts` and the mount table were identical before and after, and nothing was left in `/run`.

| Check | Observed |
|---|---|
| Daemon and all 35 descendants | uid/gid 997/988 (all four IDs), **no supplementary groups**, CapInh/Prm/Eff/Bnd/Amb all zero, NoNewPrivs 1, exe `/usr/sbin/zabbix_server` |
| Namespace and trust | private mount namespace (not init's); `/etc/ssl/certs` bind-mounted read-only; the daemon's bundle equals the lab CA |
| Delivery to a lab-CA certificate for `runner` | delivered once, with the synthetic key |
| Untrusted, other-anchor, wrong-name, expired | Zabbix TLS error; **no HTTP request** at the receiver |
| Host trust (outside the namespace) | refuses the lab certificate |
| Lifecycle | `systemctl stop` leaves no process; restart gives a new PID; SIGTERM from the lab account stops the unit |

## Installed validation (pending the owner's authorization)

These steps need `install.sh` to have run. Run each on the lab host, under the shared lab lock, with the runner idle.

```
sudo sh deploy/lab-trust/install.sh              # expect: the table above printed; exit 0
sh deploy/lab-trust/verify.sh                    # expect: "lab-trust installation matches the reviewed files"
sudo -l -U zabalack-runner                       # expect: exactly the two systemctl commands
sudo python3 lab/lab_trust_validation.py --mode installed --checkout <tree> --work <tmp> --out <file>
                                                 # expect: every case passes; host_unchanged true; the lab account
                                                 # starts zabbix_server itself through the unit
<ordinary native cycle as the lab account>       # expect: PASS, exit 0, runner-record retained, final record validates;
                                                 # the deployment webhook delivered through the unit's trust
sudo sh deploy/lab-trust/uninstall.sh            # rollback check if requested: every path gone
```

For the ordinary native cycle, and for the complete S2 real-engine acceptance at the final integration tree, keep
the original provisional and final records and every service log. Capture the precise webhook error if a delivery
fails.

The earlier native failure at `094fce4` (`synthetic_webhook_delivery_failed`) has no explicit TLS error in its
retained log. Its cause (missing lab trust) is an inference for that run, and it is not reconstructed.
