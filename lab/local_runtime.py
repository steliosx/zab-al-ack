#!/usr/bin/env python3
"""Start/stop only this repository's disposable PostgreSQL 18 cluster.

No connection to pre-existing servers, no global service management, no production
credentials. Requires an explicit isolated-lab acknowledgement for mutation.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
STATE = REPO / ".lab"
PGDATA = STATE / "pgdata"
MARKER = STATE / "ISOLATED_ACK_LAB"
PORT = 55432
# Every lab client uses loopback TCP. Disable unused Unix sockets to avoid both
# checkout/TMPDIR path limits and shared temporary-directory ownership changes.
# PostgreSQL 18 config.sgml explicitly supports the empty setting.


def binaries() -> Path:
    configured = os.environ.get("ACK_LAB_PG_BIN")
    candidates = [Path(configured)] if configured else [REPO.parent / "runtime/pg18/bin"]
    path_binary = shutil.which("pg_ctl")
    if path_binary and not configured:
        candidates.append(Path(path_binary).parent)
    for candidate in candidates:
        if (candidate / "pg_ctl").is_file():
            version = subprocess.check_output([str(candidate / "postgres"), "--version"], text=True).strip()
            if not version.startswith("postgres (PostgreSQL) 18."):
                raise SystemExit(f"PostgreSQL 18 required; found {version}")
            return candidate
    raise SystemExit("PostgreSQL 18 binaries unavailable; run lab/build_postgres.sh or set ACK_LAB_PG_BIN")


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    if os.geteuid() == 0:
        command = ["runuser", "-u", "nobody", "--", *command]
    return subprocess.run(command, check=check, text=True, capture_output=True)


def port_listening(port: int = PORT) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def postmaster_pid() -> int | None:
    try:
        return int((PGDATA / "postmaster.pid").read_text().splitlines()[0].strip())
    except (OSError, ValueError, IndexError):
        return None


def inspect(pg: Path) -> dict:
    """Classify the disposable cluster without mutating anything.

    absent     no .lab directory and nothing on the lab port
    stopped    marked lab, no server running from it, lab port free
    running    marked, initialised lab whose pg_ctl reports a live postmaster
    unmarked   a .lab directory that this tool did not declare (never reset it)
    ambiguous  anything else: an inaccessible data directory, a stale or
               unreadable PID file that pg_ctl cannot classify, or a listener on
               the lab port that is not the marked cluster
    Only `stopped` and `absent` may precede a destructive reset; `running` must
    first be stopped and re-inspected.
    """
    record: dict = {"state": "ambiguous", "marker": MARKER.exists(), "initialised": (PGDATA / "PG_VERSION").exists(),
                    "pg_ctl_status": None, "postmaster_pid": None, "port": PORT, "port_listening": port_listening()}
    if not STATE.exists():
        record["state"] = "ambiguous" if record["port_listening"] else "absent"
        record["detail"] = "lab port in use without a lab directory" if record["port_listening"] else "no lab directory"
        return record
    if not record["marker"]:
        record["state"] = "unmarked"
        record["detail"] = "existing .lab directory without the isolated-lab marker"
        return record
    if not record["initialised"]:
        record["state"] = "ambiguous" if record["port_listening"] else "stopped"
        record["detail"] = "marked lab without an initialised cluster"
        return record
    status = run([str(pg / "pg_ctl"), "-D", str(PGDATA), "status"], check=False)
    record["pg_ctl_status"] = status.returncode
    record["postmaster_pid"] = postmaster_pid()
    if status.returncode == 0:
        record["state"] = "running"
        record["detail"] = "pg_ctl reports the marked cluster running"
    elif status.returncode == 3 and not record["port_listening"]:
        # pg_ctl has already checked that any PID file names no live process.
        record["state"] = "stopped"
        record["stale_pid_file"] = record["postmaster_pid"] is not None
        record["detail"] = "pg_ctl reports no server running from the marked cluster"
    elif status.returncode == 3:
        record["detail"] = "lab port in use while the marked cluster is stopped"
    else:
        record["detail"] = "pg_ctl could not classify the marked cluster (exit %d)" % status.returncode
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "stop", "status", "state"])
    parser.add_argument("--isolated-lab", action="store_true")
    args = parser.parse_args()
    if args.command not in ("status", "state") and not args.isolated_lab:
        parser.error("mutation requires --isolated-lab")
    if os.geteuid() == 0 and args.command == "start":
        capability = subprocess.run(["runuser", "-u", "nobody", "--", "id", "-u"], capture_output=True, text=True)
        if capability.returncode:
            raise SystemExit("BLOCKED_ENVIRONMENT: isolated PostgreSQL requires a non-root OS identity; this runtime cannot create one. Run the lab on a host with an unprivileged account (tools/native_local.py, docs/local-services.md), or the Compose lab where Docker is authorised. No PostgreSQL root-safety checks are changed.")
    pg = binaries()
    if args.command == "start":
        if STATE.exists() and not MARKER.exists():
            raise SystemExit("Refusing unmarked pre-existing .lab directory")
        STATE.mkdir(mode=0o700, exist_ok=True)
        MARKER.write_text("Disposable zabalack lab. Loopback port 55432.\n")
        if os.geteuid() == 0:
            uid = pwd.getpwnam("nobody").pw_uid
            gid = pwd.getpwnam("nobody").pw_gid
            os.chown(STATE, uid, gid)
            os.chown(MARKER, uid, gid)
        if not (PGDATA / "PG_VERSION").exists():
            try:
                result = run([str(pg / "initdb"), "-D", str(PGDATA), "-U", "lab_admin", "--auth-local=trust", "--auth-host=trust", "--encoding=UTF8", "--locale=C.UTF-8"])
            except subprocess.CalledProcessError as error:
                # Keep initdb's own explanation with the lab, where the runner
                # preserves it, instead of losing it inside the traceback.
                (STATE / "initdb.log").write_text((error.stdout or "") + (error.stderr or ""))
                raise SystemExit("PostgreSQL lab initdb failed:\n" + ((error.stdout or "") + (error.stderr or ""))[-2000:]) from error
            (STATE / "initdb.log").write_text(result.stdout + result.stderr)
            config = PGDATA / "postgresql.conf"
            with config.open("a") as f:
                f.write(f"\n# Isolated repository lab only; never copy authentication to production.\nlisten_addresses='127.0.0.1'\nport={PORT}\nunix_socket_directories=''\nmax_connections=100\nshared_buffers='128MB'\nlogging_collector=off\nlog_statement='none'\n")
        if run([str(pg / "pg_ctl"), "-D", str(PGDATA), "status"], check=False).returncode != 0:
            # The startup override also handles a previously initialised,
            # stopped disposable cluster without editing/deleting its data.
            # Lab ports 55432/55433/56432 sit inside the default ephemeral
            # range; a client socket of the WASM adapter can hold 55432 in
            # TIME_WAIT for up to a minute. Retry rather than fail on that.
            for attempt in range(4):
                result = run([str(pg / "pg_ctl"), "-D", str(PGDATA), "-l", str(STATE / "postgres.log"),
                              "-o", "-c unix_socket_directories=", "-w", "start"], check=False)
                if result.returncode == 0:
                    break
                log_text = (STATE / "postgres.log").read_text() if (STATE / "postgres.log").exists() else ""
                if "Address already in use" not in log_text or attempt == 3:
                    raise SystemExit("PostgreSQL lab start failed:\n" + (result.stdout + result.stderr + log_text)[-2000:])
                time.sleep(20)
            print(result.stdout.strip())
        else:
            print("Declared isolated cluster already running")
        print(json.dumps({"host": "127.0.0.1", "port": PORT, "provisioning_user": "lab_admin", "authentication": "synthetic loopback-only trust", "production_gate": "NOT_VERIFIED"}))
    elif args.command == "stop":
        if not MARKER.exists():
            raise SystemExit("Refusing to stop unmarked data directory")
        result = run([str(pg / "pg_ctl"), "-D", str(PGDATA), "-m", "fast", "-w", "stop"], check=False)
        print(result.stdout.strip() or result.stderr.strip())
        if result.returncode:
            return result.returncode
        # A zero exit from pg_ctl is not the guarantee callers need before a
        # destructive reset; report the observed state after the stop.
        observed = inspect(pg)
        print(json.dumps(observed))
        return 0 if observed["state"] == "stopped" else 5
    elif args.command == "state":
        observed = inspect(pg)
        print(json.dumps(observed))
        return 0
    else:
        if not MARKER.exists():
            print("Declared isolated cluster has not been initialized")
            return 3
        result = run([str(pg / "pg_ctl"), "-D", str(PGDATA), "status"], check=False)
        print(result.stdout.strip() or result.stderr.strip())
        return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
