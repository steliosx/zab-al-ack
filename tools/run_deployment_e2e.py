#!/usr/bin/env python3
"""Build and run the opt-in, fresh-project deployment-path service lab."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
from uuid import uuid4
import xml.etree.ElementTree as ET

from evidence_utils import code_digests
from deployment_evidence import CHECK_NAMES, REFERENCE_VERSIONS, validate_report

ROOT = Path(__file__).resolve().parents[1]
IMAGES = {
    "postgres": "postgres:18.3-bookworm",
    "zabbix-db": "postgres:16.4-alpine",
    "zabbix-server": "zabbix/zabbix-server-pgsql:alpine-7.0.10",
    "zabbix-web": "zabbix/zabbix-web-nginx-pgsql:alpine-7.0.10",
}
EXPECTED_CASES = {
    "test_installed_authenticated_path_and_exactly_one_source_effect",
    "test_real_customer_scope_and_api_key_cannot_sign",
    "test_native_runtime_login_privileges",
    "test_real_media_macros_and_update_filter",
}


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean_environment():
    """Do not inherit Compose files, proxy targets, app config or test switches."""
    allowed = {"PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "XDG_RUNTIME_DIR"}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env["TZ"] = "UTC"
    return env


def local_docker(env):
    if not shutil.which("docker", path=env.get("PATH")):
        raise RuntimeError("docker_unavailable")
    host = env.get("DOCKER_HOST")
    if host and env.get("DOCKER_CONTEXT"):
        raise RuntimeError("docker_target_ambiguous")
    if not host:
        contexts = json.loads(subprocess.check_output(["docker", "context", "inspect"], env=env, text=True, timeout=10))
        host = contexts[0]["Endpoints"]["docker"]["Host"]
    if not isinstance(host, str) or not re.fullmatch(r"unix:///[^\x00\r\n?#]+", host):
        raise RuntimeError("docker_remote_target_refused")
    subprocess.run(["docker", "compose", "version"], env=env, check=True, capture_output=True, timeout=10)


def protected(path, value):
    path.write_text(value)
    path.chmod(0o600)


def certificates(private):
    # Generated synthetic trust only; never import host trust or production keys.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import ipaddress
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Zab-al-ack disposable deployment lab")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("runner"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256()))
    (private / "ca").mkdir(mode=0o755)
    cert = certificate.public_bytes(serialization.Encoding.PEM)
    (private / "ca/lab.crt").write_bytes(cert)
    protected(private / "server.key", key.private_bytes(serialization.Encoding.PEM,
              serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())
    (private / "server.crt").write_bytes(cert)


def compose_spec(private, context, project):
    """No ports, host network, Docker socket mounts, or shared production volumes."""
    mounts = [{"type": "bind", "source": str(private), "target": "/run/zabalack-e2e"}]
    pg = {"image": IMAGES["postgres"], "environment": {"POSTGRES_DB": "alerta", "POSTGRES_USER": "lab_admin",
          "POSTGRES_PASSWORD_FILE": "/run/secrets/pg-password", "POSTGRES_HOST_AUTH_METHOD": "scram-sha-256"},
          "volumes": ["alerta-data:/var/lib/postgresql", {"type": "bind", "source": str(private / "pg-password"),
                      "target": "/run/secrets/pg-password", "read_only": True}],
          "healthcheck": {"test": ["CMD-SHELL", "pg_isready -U lab_admin -d alerta"], "interval": "2s", "timeout": "2s", "retries": 45}}
    zdb = {"image": IMAGES["zabbix-db"], "environment": {"POSTGRES_DB": "zabbix", "POSTGRES_USER": "zabbix",
           "POSTGRES_PASSWORD_FILE": "/run/secrets/zdb-password", "POSTGRES_HOST_AUTH_METHOD": "scram-sha-256"},
           "volumes": ["zabbix-data:/var/lib/postgresql/data", {"type": "bind", "source": str(private / "zdb-password"),
                       "target": "/run/secrets/zdb-password", "read_only": True}],
           "healthcheck": {"test": ["CMD-SHELL", "pg_isready -U zabbix -d zabbix"], "interval": "2s", "timeout": "2s", "retries": 45}}
    zenv = {"DB_SERVER_HOST": "zabbix-db", "POSTGRES_DB": "zabbix", "POSTGRES_USER": "zabbix",
            "POSTGRES_PASSWORD_FILE": "/run/secrets/zdb-password"}
    zmount = [{"type": "bind", "source": str(private / "zdb-password"), "target": "/run/secrets/zdb-password", "read_only": True}]
    services = {"postgres": pg, "zabbix-db": zdb,
        "zabbix-server": {"image": IMAGES["zabbix-server"], "environment": zenv,
                          "volumes": zmount,
                          "depends_on": {"zabbix-db": {"condition": "service_healthy"}}},
        "zabbix-web": {"image": IMAGES["zabbix-web"], "environment": zenv | {"ZBX_SERVER_HOST": "zabbix-server", "PHP_TZ": "UTC"},
                       "volumes": zmount, "depends_on": ["zabbix-server"]},
        "runner": {"build": {"context": str(context), "dockerfile": "lab/deployment_e2e.Dockerfile"},
                   "image": project + "-runner", "volumes": mounts,
                   "environment": {"TZ": "UTC", "ACK_DEPLOYMENT_E2E": "GENERATED_ISOLATED_PROJECT"},
                   "depends_on": {"postgres": {"condition": "service_healthy"}}}}
    for service in services.values():
        service["networks"] = ["isolated"]
    return {"name": project, "services": services, "networks": {"isolated": {"internal": True}},
            "volumes": {"alerta-data": {}, "zabbix-data": {}}}


def staged_context(destination):
    destination.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE", "NOTICE", "MANIFEST.in", "requirements.lock"):
        shutil.copy2(ROOT / name, destination / name)
    shutil.copytree(ROOT / "src", destination / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "*.pyc"))
    shutil.copytree(ROOT / "sql", destination / "sql", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (destination / "lab").mkdir()
    for path in sorted((ROOT / "lab").glob("deployment_e2e*")):
        if path.is_file():
            shutil.copy2(path, destination / "lab" / path.name)
    shutil.copy2(ROOT / "lab/zabbix_roles.py", destination / "lab/zabbix_roles.py")
    shutil.copytree(ROOT / "zabbix", destination / "zabbix")
    shutil.copytree(ROOT / "tests/deployment_e2e", destination / "tests/deployment_e2e", ignore=shutil.ignore_patterns("__pycache__"))


def validate_result(private, exit_code):
    raw = json.loads((private / "observations.json").read_text())
    # Export only booleans and fixed version/role enums. Raw service output is private.
    checks = raw.get("checks")
    expected = CHECK_NAMES
    if not isinstance(checks, dict) or set(checks) != expected or any(type(v) is not bool for v in checks.values()):
        raise ValueError("observation_shape")
    cases = list(ET.parse(private / "junit.xml").getroot().iter("testcase"))
    if len(cases) != len(EXPECTED_CASES) or {case.get("name") for case in cases} != EXPECTED_CASES:
        raise ValueError("unexpected_test_selection")
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for case in cases:
        kind = "failed" if case.find("failure") is not None or case.find("error") is not None else "skipped" if case.find("skipped") is not None else "passed"
        counts[kind] += 1
    if raw.get("versions") != REFERENCE_VERSIONS:
        raise ValueError("reference_version_mismatch")
    status = "PASS" if exit_code == 0 and counts["passed"] == len(EXPECTED_CASES) and all(checks.values()) else "FAIL"
    return {"status": status, "counts": counts, "checks": checks, "versions": raw["versions"]}


def stop_runner(project, name, env):
    """Compose stop may omit one-offs. Stop only our exact named/labeled runner."""
    listing = subprocess.check_output(["docker", "ps", "--all", "--quiet", "--no-trunc",
        "--filter", "label=com.docker.compose.project=" + project, "--filter", "name=" + name],
        env=env, text=True, timeout=10).split()
    if len(listing) > 1 or any(not re.fullmatch("[0-9a-f]{64}", item) for item in listing):
        raise ValueError("runner_identity_ambiguous")
    for identity in listing:
        info = json.loads(subprocess.check_output(["docker", "inspect", identity], env=env, text=True, timeout=10))
        if (len(info) != 1 or info[0].get("Name") != "/" + name
                or info[0].get("Config", {}).get("Labels", {}).get("com.docker.compose.project") != project
                or info[0].get("Config", {}).get("Labels", {}).get("com.docker.compose.service") != "runner"):
            raise ValueError("runner_identity_mismatch")
        subprocess.run(["docker", "stop", "--time", "10", identity], env=env, check=True,
                       capture_output=True, timeout=25)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated-lab", action="store_true", required=True)
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    if args.plan:
        print(json.dumps({"images": IMAGES, "application": "installed factory via Flask CLI HTTPS process",
            "worker": "installed zabalack CLI process", "auth": "real signup/login/customer/key API",
            "network": "fresh internal Compose network; no published ports", "source_writes": "fresh synthetic Zabbix only",
            "shutdown": "stop only this generated project; retain volumes and private logs", "G1_G8": "MANUAL_GATE_PENDING"}, indent=2))
        return 0
    report_path = ROOT / "evidence/deployment-e2e-run.json"
    report_path.parent.mkdir(exist_ok=True)
    report = {"layer": "native_deployment_e2e", "status": "RUNNING", "started_at": stamp(),
              "code_digests": code_digests(ROOT), "deployment_gates": "MANUAL_GATE_PENDING"}
    def save():
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    save()  # invalidate any old PASS before Docker lookup or secret generation
    env = clean_environment()
    try:
        local_docker(env)
    except (RuntimeError, OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        report.update(status="NOT_RUN", reason="local_docker_unavailable_or_refused", finished_at=stamp())
        save()
        print(json.dumps(report))
        return 2
    run_id = uuid4().hex[:12]
    project = "zabalack-e2e-" + run_id
    private = ROOT / "build/private" / project
    private.mkdir(parents=True, mode=0o700)
    private.chmod(0o700)
    context = private / "context"
    command = ["docker", "compose", "-p", project, "-f", str(private / "compose.json")]
    report.update(project=project, counts=None)
    runner_launched = False
    runner_name = project + "-runner"
    try:
        protected(private / "pg-password", secrets.token_urlsafe(32))
        protected(private / "zdb-password", secrets.token_urlsafe(32))
        # This sole file is mounted read-only into the non-root Zabbix images.
        # Host secrecy comes from the generated parent directory's mode 0700.
        (private / "zdb-password").chmod(0o444)
        protected(private / "bootstrap.json", json.dumps({"isolated_lab": True, "project": project,
                  "passwords": {name: secrets.token_urlsafe(32) for name in ("alerta_app", "zabalack_worker", "zabalack_probe", "zabalack_operator", "migrator", "human", "ingest", "outside", "admin")}}))
        certificates(private)
        staged_context(context)
        protected(private / "compose.json", json.dumps(compose_spec(private, context, project)))
        with (private / "compose.log").open("w") as log:
            def run(arguments, timeout):
                return subprocess.run([*command, *arguments], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=True)
            run(["build", "runner"], 900)
            run(["up", "-d", "--wait", "--wait-timeout", "180", "postgres", "zabbix-db", "zabbix-server", "zabbix-web"], 240)
            runner_launched = True
            result = subprocess.run([*command, "run", "--rm", "--name", runner_name, "--use-aliases", "runner"], cwd=ROOT, env=env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=600)
            report.update(validate_result(private, result.returncode))
        report["image_ids"] = {}
        for name, image in IMAGES.items():
            ids = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", image], env=env, text=True, timeout=10).strip()
            if not re.fullmatch("sha256:[0-9a-f]{64}", ids):
                raise ValueError("image_identity")
            report["image_ids"][name] = ids
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, ET.ParseError):
        report.update(status="FAIL", reason="lab_execution_or_validation_failed")
    except KeyboardInterrupt:
        report.update(status="FAIL", reason="lab_interrupted")
    finally:
        if runner_launched:
            try:
                stop_runner(project, runner_name, env)
                report["runner_shutdown_exit_code"] = 0
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                report.update(status="FAIL", reason="runner_shutdown_failed", runner_shutdown_exit_code=1)
        try:
            with (private / "shutdown.log").open("w") as log:
                stopped = subprocess.run([*command, "stop", "--timeout", "10"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=60)
            report["shutdown_exit_code"] = stopped.returncode
            if stopped.returncode:
                report.update(status="FAIL", reason="lab_shutdown_failed")
        except (OSError, subprocess.SubprocessError):
            report.update(status="FAIL", reason="lab_shutdown_failed")
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
    print(json.dumps(report))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
