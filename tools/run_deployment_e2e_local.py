#!/usr/bin/env python3
"""Run the deployment-path lab on locally provided services, without Docker.

Non-Docker form of tools/run_deployment_e2e.py (docs/RELEASE_AMENDMENTS.md R14).
It executes the unmodified lab/deployment_e2e_runtime.py through pytest
tests/deployment_e2e exactly as the Compose runner does, but the services
are supplied by the operator on one host:

  * PostgreSQL 18 reachable as host `postgres`, port 5432, superuser
    `lab_admin` whose password is in <private>/pg-password, with an empty
    database `alerta` (host logins must be password-authenticated);
  * a fresh Zabbix 7.0.10 frontend reachable as http://zabbix-web:8080 with the
    default Admin credentials, backed by a running zabbix_server;
  * the hostnames `postgres`, `zabbix-web` and `runner` resolving to loopback;
  * /opt/zabalack-lab resolving to this checkout and <private> being
    /run/zabalack-e2e with mode 0700 (both fixed in lab/deployment_e2e_runtime.py);
  * a runner Python environment (--runner-python) in which zabalack is installed
    from the wheel named by --wheel, not editably: the installed package bytes are
    compared with the wheel and the record names the wheel's SHA-256.

The optional --reset-command is the operator's script that returns Zabbix and
the `alerta` database to a fresh state before the run; --services-file names
the postgres, zabbix-db, zabbix-server and zabbix-web components. The record is
labelled `not_docker: true` and carries no image identities; it is validated by
tools/deployment_evidence.py like every other deployment record.
"""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import PathDistribution
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

from deployment_evidence import IMAGE_COMPONENTS, validate_report
from evidence_utils import code_digests
from run_deployment_e2e import certificates, protected, stamp, validate_result

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = Path("/run/zabalack-e2e")
LINK = Path("/opt/zabalack-lab")
PASSWORD_NAMES = ("alerta_app", "zabalack_worker", "zabalack_probe", "zabalack_operator", "migrator", "human", "ingest", "outside", "admin")
GENERATED = ("bootstrap.json", "server.crt", "server.key", "junit.xml", "observations.json", "signing.key", "source.token",
             "probe.dsn", "worker.dsn", "operator.dsn", "zabalack.json", "alertad.conf")
LAB_PORTS = (58081, 58443)


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def check_host_layout():
    require(LINK.is_symlink() or LINK.is_dir(), "lab_link_missing: /opt/zabalack-lab must resolve to this checkout")
    require(LINK.resolve() == ROOT, "lab_link_mismatch: /opt/zabalack-lab resolves elsewhere")
    require(PRIVATE.is_dir() and (PRIVATE.stat().st_mode & 0o777) == 0o700, "private_dir_required: /run/zabalack-e2e mode 0700")
    require((PRIVATE / "pg-password").is_file(), "pg_password_missing: /run/zabalack-e2e/pg-password")
    for name in ("postgres", "zabbix-web", "runner"):
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(name, None)}
        except socket.gaierror:
            addresses = set()
        require(addresses and addresses <= {"127.0.0.1", "::1"}, "hostname_not_loopback: " + name)
    for port in LAB_PORTS:
        with socket.socket() as sock:
            sock.settimeout(0.5)
            require(sock.connect_ex(("127.0.0.1", port)) != 0, "lab_port_in_use_before_run: " + str(port))


def wheel_identity(wheel: Path) -> tuple[str, dict[str, bytes]]:
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if name.startswith("zabalack/") and name.endswith(".py")]
        require(names, "wheel_has_no_package")
        contents = {name: archive.read(name) for name in names}
        metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        version = re.search(r"^Version: (.+)$", archive.read(metadata).decode(), re.M)
    require(version is not None, "wheel_version_missing")
    return digest, contents


def check_runner(python: Path, wheel: Path) -> dict:
    """The runner must import the installed wheel, never this checkout."""
    require(python.is_file() and os.access(python, os.X_OK), "runner_python_missing")
    probe = ("import json, zabalack, importlib.metadata as m; d = m.distribution('zabalack'); "
             "print(json.dumps({'file': zabalack.__file__, 'version': d.version, 'location': str(d.locate_file('')), "
             "'direct_url': d.read_text('direct_url.json')}))")
    result = subprocess.run([str(python), "-c", probe], cwd="/", env={"PATH": os.environ["PATH"], "TZ": "UTC"},
                            text=True, capture_output=True, timeout=60)
    require(result.returncode == 0, "runner_cannot_import_zabalack")
    info = json.loads(result.stdout)
    installed = Path(info["file"]).resolve().parent
    require(not installed.is_relative_to(ROOT), "runner_imports_checkout_not_wheel")
    if info["direct_url"]:
        require(not json.loads(info["direct_url"]).get("dir_info", {}).get("editable"), "runner_install_is_editable")
    digest, contents = wheel_identity(wheel)
    for name, data in contents.items():
        require((installed.parent / name).read_bytes() == data, "installed_bytes_differ_from_wheel: " + name)
    record_path = Path(info["location"]) / ("zabalack-" + info["version"] + ".dist-info")
    require(record_path.is_dir(), "installed_distribution_metadata_missing")
    entry_points = {entry.name for entry in PathDistribution(record_path).entry_points if entry.group == "alerta.plugins"}
    require(entry_points == {"zabalack_zabbix_normalise", "zabalack_zabbix_ack"}, "installed_entry_points_missing")
    return {"python": str(python), "version": info["version"], "wheel_sha256": digest, "location": str(installed.parent)}


def services(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text())
    require(isinstance(data, dict) and set(data) == IMAGE_COMPONENTS
            and all(isinstance(value, str) and 0 < len(value.strip()) <= 4096 for value in data.values()),
            "services_file_shape: exactly postgres, zabbix-db, zabbix-server, zabbix-web descriptions")
    return {key: value.strip() for key, value in data.items()}


def clean_private():
    for name in GENERATED:
        (PRIVATE / name).unlink(missing_ok=True)
    for path in PRIVATE.glob("*.log"):
        path.unlink()
    ca = PRIVATE / "ca"
    if ca.is_dir():
        for path in ca.iterdir():
            path.unlink()
        ca.rmdir()


def bootstrap(project: str):
    import secrets
    protected(PRIVATE / "bootstrap.json", json.dumps({"isolated_lab": True, "project": project,
              "passwords": {name: secrets.token_urlsafe(32) for name in PASSWORD_NAMES}}))
    certificates(PRIVATE)


def lab_ports_free() -> bool:
    for port in LAB_PORTS:
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--isolated-lab", action="store_true", required=True)
    parser.add_argument("--runner-python", type=Path, required=True, help="Python of an environment with the wheel installed non-editably")
    parser.add_argument("--wheel", type=Path, required=True, help="the built zabalack wheel the runner environment installed")
    parser.add_argument("--services-file", type=Path, required=True, help="JSON naming the postgres, zabbix-db, zabbix-server and zabbix-web services")
    parser.add_argument("--reset-command", default=None, help="operator command returning Zabbix and the alerta database to a fresh state")
    parser.add_argument("--plan", action="store_true", help="Validate inputs and print the plan without running")
    args = parser.parse_args(argv)
    evidence = ROOT / "evidence"
    evidence.mkdir(exist_ok=True)
    record_path = evidence / "deployment-e2e-run.json"
    report = {"layer": "native_deployment_e2e", "status": "RUNNING", "started_at": stamp(), "not_docker": True,
              "code_digests": code_digests(ROOT), "deployment_gates": "MANUAL_GATE_PENDING"}

    def save():
        record_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    if not args.plan:
        save()  # invalidate the previous record before any service is touched
    try:
        components = services(args.services_file)
        check_host_layout()
        # Never resolve the interpreter path: a virtual environment python is a symlink to its base interpreter.
        runner_python = Path(os.path.abspath(args.runner_python))
        runner = check_runner(runner_python, args.wheel.resolve())
    except (RuntimeError, OSError, ValueError, KeyError, StopIteration) as error:
        report.update(status="NOT_RUN", reason=str(error)[:300], finished_at=stamp())
        report.pop("code_digests", None)
        if not args.plan:
            save()
        print(json.dumps(report))
        return 2
    # Public description: no interpreter or site-packages path leaves the host.
    components["runner"] = ("this host: a Python 3.12 environment with requirements.lock and the built zabalack "
                            + runner["version"] + " wheel installed non-editably (sha256 " + runner["wheel_sha256"]
                            + "); installed package bytes verified against the wheel; provides the Alerta entry points, "
                            "zabalack CLI and Flask CLI HTTPS server")
    project = "zabalack-e2e-" + os.urandom(6).hex()
    if args.plan:
        print(json.dumps({"project": project, "runner": runner, "components": components, "reset_command": args.reset_command,
                          "tests": "tests/deployment_e2e through lab/deployment_e2e_runtime.py", "external_zabbix_writes": "isolated lab only"}, indent=2))
        return 0
    report.update(project=project, runner="lab/deployment_e2e_runtime.py executed unmodified through pytest tests/deployment_e2e on one host "
                  "without Docker (tools/run_deployment_e2e_local.py): the Compose services were replaced by operator-provided local "
                  "services with the same hostnames (postgres, zabbix-web, runner) and the same private directory; the runner environment "
                  "installed the built wheel non-editably; see docs/RELEASE_AMENDMENTS.md R14",
                  components=components, image_ids=None)
    private_logs = ROOT / "build/private" / project
    private_logs.mkdir(parents=True, mode=0o700)
    try:
        clean_private()
        bootstrap(project)
        if args.reset_command:
            with (private_logs / "reset.log").open("w") as log:
                subprocess.run(shlex.split(args.reset_command), cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
        env = {"PATH": str(runner_python.parent) + os.pathsep + os.environ["PATH"], "HOME": "/tmp", "TZ": "UTC",
               "PYTHONUNBUFFERED": "1", "ACK_DEPLOYMENT_E2E": "GENERATED_ISOLATED_PROJECT"}
        with (private_logs / "pytest.log").open("w") as log:
            result = subprocess.run([str(runner_python), "-m", "pytest", "tests/deployment_e2e", "-q", "-p", "no:cacheprovider",
                                     "--junitxml=" + str(PRIVATE / "junit.xml")], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=900)
        # The runner process is the pytest child itself; it has exited here.
        report["runner_shutdown_exit_code"] = 0
        report.update(validate_result(PRIVATE, result.returncode))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, ET.ParseError):
        report.update(status="FAIL", reason="lab_execution_or_validation_failed")
    except KeyboardInterrupt:
        report.update(status="FAIL", reason="lab_interrupted")
    finally:
        # The runtime terminates its own Alerta, worker and TLS-proxy children;
        # cleanup succeeded only when no lab listener remains.
        report["shutdown_exit_code"] = 0 if lab_ports_free() else 1
        if report["shutdown_exit_code"]:
            report.update(status="FAIL", reason="lab_processes_still_listening")
        report["tree_unchanged"] = report["code_digests"] == code_digests(ROOT)
        if not report["tree_unchanged"]:
            report.update(status="FAIL", reason="source_changed_during_run")
        report["finished_at"] = stamp()
        if report["status"] == "PASS":
            try:
                validate_report(report, ROOT)
            except ValueError:
                report.update(status="FAIL", reason="final_evidence_validation_failed")
        save()
    print(json.dumps({key: report.get(key) for key in ("status", "counts", "tree_unchanged", "reason", "project")}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
