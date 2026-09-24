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
    requested executable, and verifies the daemon by the unit's argv.
  - **Unit ownership.** The unit is global, so the lab acts on it only when it is inactive or is this lab's own
    invocation. It reads systemd's view without privilege (`systemctl show`: ActiveState, MainPID, InvocationID,
    ControlGroup). As soon as its own start returns, it records the invocation ID, control group, main PID and
    that process's start time in `.zabbix-lab/trust-unit.owner.json`. An active, activating or deactivating unit
    under any other invocation, or one systemd cannot report, makes the lab state `ambiguous`. The lab then does
    not write the fixed configuration, start, signal or stop anything. The lab stops the unit only when the
    invocation is its own, including after its main process has exited while descendants remain in the control
    group (the stop ends the whole group). The stop succeeds only when systemd then reports the unit inactive.
    The shared lab lock serializes cooperating jobs; the invocation binding is what identifies this run's unit.
  - `tools/run_deployment_e2e.py` signs the run's `runner`/127.0.0.1 certificate with the lab CA. `server.crt`
    holds the leaf followed by the CA, so the lab's own clients are unchanged.
  - Without the unit, the deployment lab's layout check refuses the cycle before any service starts
    (`layout_not_ready`, naming this step), and the local runner refuses with `lab_trust_not_provisioned`.
- **Existing privilege context:** the lab account is a member of the `docker` group. The unit's daemon does not
  keep that membership (supplementary groups are cleared), but the account keeps it. This page makes no
  least-privilege claim about the account, and does not change its membership.

## Installation diff (what `install.sh` creates; it refuses if any path exists)

`install.sh` installs only from an immutable, owner-approved snapshot. It takes one argument: the SHA-256 of
`deploy/lab-trust/SHA256SUMS` that the owner approved. `snapshot-check.sh` then requires three things:
- the snapshot directory and every ancestor are root-owned and not writable by group or others (a sticky
  ancestor such as `/tmp` is allowed);
- each of the eight sources is a root-owned regular file that is not group- or other-writable;
- every source matches `SHA256SUMS`.

A runner-writable checkout is refused, and so is a manifest other than the approved one.

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

**Audit:** after installing, the operator runs `deploy/lab-trust/verify.sh` **as root**, from the same snapshot.
It reads the 0440 sudoers file and the CA key, so the lab account cannot run it, and no sudo rule is added for
it. It is read-only and checks:
- owners, modes and types, including the hash link;
- the exact entries of `/etc/zabalack-lab`;
- the unit, sudoers and tmpfiles contents against the snapshot;
- through `check-ca.sh`:
  - the trust directory holds exactly `ca-certificates.crt`, `lab-ca.pem` and `<subject-hash>.0`, the last a
    link to `lab-ca.pem`;
  - the CA directory holds exactly the key and the certificate;
  - all three certificate files are one identical, self-signed certificate that matches the key;
  - the extensions are exactly the key identifiers plus `CA:TRUE, pathlen:0`, `Certificate Sign, CRL Sign`
    and names permitted only for `DNS:runner` and `IP:127.0.0.1/255.255.255.255`, each critical.

It prints each installed file's SHA-256. A CA with any other name, path length, key usage or extension fails.

**Rollback:** `deploy/lab-trust/uninstall.sh` stops only this unit and then requires systemd to report it
inactive (or failed), with no process left in its control group. Otherwise it removes nothing, exits 1 and
reports that the rollback is not complete. Only then does it remove the paths in the table plus
`/run/zabalack-lab-zabbix` and reload systemd. It finally checks that every path is gone and the unit is no longer
loaded. It never touches the global trust store, AppArmor or any caller-selected directory.

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
SNAP=/root/zabalack-lab-trust-<approved commit>  # as root: a root-owned snapshot of the approved commit
sudo sh -c "umask 022 && mkdir $SNAP && git -C <checkout> archive <approved commit> | tar -x -C $SNAP"
sudo sha256sum $SNAP/deploy/lab-trust/SHA256SUMS # expect: the digest the owner approved
sudo sh $SNAP/deploy/lab-trust/install.sh <approved digest>
                                                 # expect: the table above printed; exit 0
sudo sh $SNAP/deploy/lab-trust/verify.sh         # expect: "lab-trust installation matches the reviewed files" and the
                                                 # installed SHA-256s; keep its full output
sudo -l -U zabalack-runner                       # expect: exactly the two systemctl commands
sudo python3 $SNAP/lab/lab_trust_validation.py --mode installed --checkout $SNAP --work <tmp> --out <file>
                                                 # expect: status PASS, 12 of 12 required cases, host_unchanged true;
                                                 # it refuses before any start if the audit fails
<ordinary native cycle as the lab account>       # expect: PASS, exit 0, runner-record retained, final record validates;
                                                 # the deployment webhook delivered through the unit's trust
sudo sh $SNAP/deploy/lab-trust/uninstall.sh      # rollback check if requested: unit inactive, every path gone
```

The installed validation's required cases are:
- `installation-audit`;
- identity and namespace;
- the five deliveries;
- host trust;
- lifecycle;
- `installed-lab-unit-ownership`: the lab refuses to stop an invocation root started, then starts and stops
  its own;
- cleanup;
- the host comparison.

An exception is recorded as the run's error, and the run does not pass.

**Private evidence.** Every attempt is collected into a PRIVATE bundle before anything is cleaned up, whatever its
outcome (PASS, FAIL, REFUSED or NOT_RUN). The bundle holds:
- the original provisional and final records;
- the retention diagnostic;
- retained-phase manifests and copies;
- the lab's service logs, status and ownership records;
- the driver's result.

`tools/collect_attempt_evidence.py` does this. It records a digest for every file and the absence of every
expected one. Secret-looking names are listed, not copied. Directories are 0700 and files 0600, with a
`PRIVATE.txt` marker.
- Both root drivers call it after the lab stops and before it is wiped, including after a refusal. They write
  to `--evidence DIR`, by default next to `--out` as `OUT.private-evidence`.
- For an ordinary native cycle, the operator runs it before removing the temporary checkout:
  `python3 tools/collect_attempt_evidence.py --bundle <private dir> --attempt <label> --checkout checkout=<tree>`.

Keep the bundle under the private evidence directory, never in a checkout, GitHub or chat. If a delivery fails,
also capture the precise webhook error.

The earlier native failure at `094fce4` (`synthetic_webhook_delivery_failed`) has no explicit TLS error in its
retained log. Its cause (missing lab trust) is an inference for that run, and it is not reconstructed.
