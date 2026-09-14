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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "stop", "status"])
    parser.add_argument("--isolated-lab", action="store_true")
    args = parser.parse_args()
    if args.command != "status" and not args.isolated_lab:
        parser.error("mutation requires --isolated-lab")
    if os.geteuid() == 0 and args.command == "start":
        capability = subprocess.run(["runuser", "-u", "nobody", "--", "id", "-u"], capture_output=True, text=True)
        if capability.returncode:
            raise SystemExit("BLOCKED_ENVIRONMENT: isolated PostgreSQL requires a non-root OS identity; this runtime cannot create one. Use lab/compose.postgres.yml on an authorised development host. No PostgreSQL root-safety checks are changed.")
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
            result = run([str(pg / "initdb"), "-D", str(PGDATA), "-U", "lab_admin", "--auth-local=trust", "--auth-host=trust", "--encoding=UTF8", "--locale=C.UTF-8"])
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
        return result.returncode
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
