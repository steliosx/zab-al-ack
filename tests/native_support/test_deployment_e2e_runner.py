"""Offline runner checks; these are not native service evidence."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("deployment_e2e_runner", ROOT / "tools/run_deployment_e2e.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def sample_result(path, *, failed=False, skipped=False, extra=False):
    names = {
        "authenticated_capture", "exactly_one_effect", "projection", "repeat_409", "cross_customer_denied",
        "ingest_key_local_only", "worker_denied_table_update", "probe_denied_table_read", "separate_native_logins",
        "media_macro_ingestion", "neutral_update_filtered", "human_update_recorded", "recovery_macro_ingestion",
    }
    report = {"checks": dict.fromkeys(names, True), "versions": {"alerta": "9.1.0", "zabbix": "7.0.10", "postgres_major": "18"},
              "private_password": "do-not-export-example"}
    (path / "observations.json").write_text(json.dumps(report))
    suite = ET.Element("testsuite")
    for index, name in enumerate(sorted(runner.EXPECTED_CASES)):
        case = ET.SubElement(suite, "testcase", name=name)
        if index == 0 and (failed or skipped):
            child = ET.SubElement(case, "failure" if failed else "skipped", message="private-token-do-not-export")
            child.text = "postgresql://private.example/secret"
    if extra:
        ET.SubElement(suite, "testcase", name="injected_unrelated_test")
    ET.ElementTree(suite).write(path / "junit.xml")


def test_plan_never_probes_daemon_or_opens_secrets(monkeypatch, capsys):
    monkeypatch.setattr(runner, "local_docker", lambda *_: pytest.fail("plan contacted Docker"))
    monkeypatch.setattr(runner, "certificates", lambda *_: pytest.fail("plan made secrets"))
    assert runner.main(["--isolated-lab", "--plan"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["G1_G8"] == "MANUAL_GATE_PENDING"


def test_staged_context_imports_role_verifier(tmp_path):
    """The shipped Docker context must include the provisioner's imported helper."""
    destination = tmp_path / "context"
    runner.staged_context(destination)
    probe = """import sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from lab import deployment_e2e_zabbix, zabbix_roles
assert Path(deployment_e2e_zabbix.__file__).resolve().is_relative_to(root)
assert Path(zabbix_roles.__file__).resolve().is_relative_to(root)
"""
    subprocess.run([sys.executable, "-I", "-c", probe, str(destination)],
                   cwd=destination, check=True, capture_output=True, timeout=30)


def test_opt_in_required_before_docker(monkeypatch):
    monkeypatch.setattr(runner, "local_docker", lambda *_: pytest.fail("missing opt-in contacted Docker"))
    with pytest.raises(SystemExit):
        runner.main([])


def test_unavailable_runtime_invalidates_prior_pass(tmp_path, monkeypatch):
    (tmp_path / "evidence").mkdir()
    output = tmp_path / "evidence/deployment-e2e-run.json"
    output.write_text(json.dumps({"status": "PASS", "private_secret": "must disappear"}))
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    def unavailable(_):
        assert json.loads(output.read_text())["status"] == "RUNNING"
        raise RuntimeError("private daemon connection message must not escape")
    monkeypatch.setattr(runner, "local_docker", unavailable)
    assert runner.main(["--isolated-lab"]) == 2
    report = json.loads(output.read_text())
    assert report["status"] == "NOT_RUN"
    assert report["reason"] == "local_docker_unavailable_or_refused"
    assert "private" not in output.read_text()


@pytest.mark.parametrize("host", ["tcp://127.0.0.1:2375", "ssh://user@host", "unix://remote/tmp/docker.sock", "unix:///tmp/socket?remote=x"])
def test_remote_or_ambiguous_daemon_refused_before_execution(host, monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda *_, **__: "/usr/bin/docker")
    monkeypatch.setattr(runner.subprocess, "run", lambda *_, **__: pytest.fail("unsafe daemon contacted"))
    with pytest.raises(RuntimeError):
        runner.local_docker({"DOCKER_HOST": host})


def test_remote_context_refused_after_read_only_inspection(monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda *_, **__: "/usr/bin/docker")
    monkeypatch.setattr(runner.subprocess, "check_output", lambda *_, **__: json.dumps([{"Endpoints": {"docker": {"Host": "ssh://remote"}}}]))
    monkeypatch.setattr(runner.subprocess, "run", lambda *_, **__: pytest.fail("remote compose executed"))
    with pytest.raises(RuntimeError, match="remote"):
        runner.local_docker({})


def test_unsafe_environment_not_forwarded(monkeypatch):
    for name in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "ACK_TEST_ADMIN_DSN", "ACK_DEPLOYMENT_E2E", "ALERTA_SVR_CONF_FILE", "DATABASE_URL", "PYTHONPATH", "PYTEST_ADDOPTS"):
        monkeypatch.setenv(name, "unsafe-inherited-value")
    result = runner.clean_environment()
    assert "unsafe-inherited-value" not in result.values()
    assert result["TZ"] == "UTC"


def test_compose_can_only_reach_fresh_project_services(tmp_path):
    spec = runner.compose_spec(tmp_path, tmp_path / "context", "zabalack-e2e-0123456789ab")
    assert spec["networks"] == {"isolated": {"internal": True}}
    for service in spec["services"].values():
        assert "ports" not in service and "network_mode" not in service and "privileged" not in service
        assert service["networks"] == ["isolated"]
        for mount in service.get("volumes", []):
            if isinstance(mount, dict):
                assert Path(mount["source"]).is_relative_to(tmp_path)
                assert "docker.sock" not in mount["source"]
    assert spec["services"]["postgres"]["environment"]["POSTGRES_HOST_AUTH_METHOD"] == "scram-sha-256"


def test_runner_identity_uses_private_owner_without_posix_process_queries(tmp_path, monkeypatch):
    original_stat = Path.stat

    def private_owner(path, **kwargs):
        if path == tmp_path:
            return SimpleNamespace(st_uid=1234, st_gid=5678)
        return original_stat(path, **kwargs)

    monkeypatch.setattr(Path, "stat", private_owner)
    # Compose construction also runs in offline Windows checks where these
    # process APIs do not exist. The generated directory supplies both IDs.
    monkeypatch.delattr(os, "geteuid", raising=False)
    monkeypatch.delattr(os, "getegid", raising=False)
    spec = runner.compose_spec(tmp_path, tmp_path / "context", "zabalack-e2e-0123456789ab")
    assert spec["services"]["runner"]["user"] == "1234:5678"
    assert all("user" not in service for name, service in spec["services"].items() if name != "runner")


def generated_bootstrap(private):
    private.mkdir(mode=0o700)
    runner.protected(private / "bootstrap.json", json.dumps({
        "isolated_lab": True, "project": "zabalack-e2e-0123456789ab",
        "passwords": dict.fromkeys(("alerta_app", "zabalack_worker", "zabalack_probe", "zabalack_operator",
                                    "migrator", "human", "ingest", "outside", "admin"), "synthetic-password"),
    }))


@pytest.mark.skipif(os.name != "posix", reason="Numeric child identity requires POSIX")
def test_compose_identity_can_read_bootstrap_and_write_host_readable_private_output(tmp_path):
    """Exercise real child credentials and unchanged protected-file checks, without Docker."""
    private = tmp_path / "private"
    generated_bootstrap(private)
    spec = runner.compose_spec(private, private / "context", "zabalack-e2e-0123456789ab")
    uid, gid = map(int, spec["services"]["runner"].get("user", "0:0").split(":"))
    probe = """import json, os, stat, sys
from pathlib import Path
root, private = map(Path, sys.argv[1:])
sys.path[:0] = [str(root), str(root / 'src')]
from lab import deployment_e2e_runtime as runtime
from zabalack.config import read_protected_file
runtime.PRIVATE = private
os.environ['ACK_DEPLOYMENT_E2E'] = 'GENERATED_ISOLATED_PROJECT'
assert runtime.require_generated_lab()['isolated_lab'] is True
output = Path(runtime.secret('observations.json', json.dumps({'verified': True})))
assert json.loads(read_protected_file(str(output))) == {'verified': True}
assert output.stat().st_uid == os.geteuid()
assert output.stat().st_gid == os.getegid()
assert stat.S_IMODE(output.stat().st_mode) == 0o600
"""
    subprocess.run([sys.executable, "-I", "-c", probe, str(ROOT), str(private)],
                   user=uid, group=gid, check=True, capture_output=True, timeout=30)
    output = private / "observations.json"
    assert json.loads(output.read_text()) == {"verified": True}
    assert output.stat().st_uid == (private / "bootstrap.json").stat().st_uid == uid
    assert output.stat().st_gid == (private / "bootstrap.json").stat().st_gid == gid
    assert stat.S_IMODE(output.stat().st_mode) == stat.S_IMODE((private / "bootstrap.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(private.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name != "posix", reason="Protected-file ownership checks require POSIX")
def test_generated_bootstrap_rejects_mismatched_effective_owner(tmp_path, monkeypatch):
    from lab import deployment_e2e_runtime as runtime
    from zabalack.config import ConfigError

    private = tmp_path / "private"
    generated_bootstrap(private)
    if private.stat().st_uid == 0:
        pytest.skip("Root-owned secrets are intentionally accepted by the protected-file contract")
    monkeypatch.setattr(runtime, "PRIVATE", private)
    monkeypatch.setenv("ACK_DEPLOYMENT_E2E", "GENERATED_ISOLATED_PROJECT")
    # Keep a real non-root-owned 0600 file/open; model the image's former root
    # effective identity only at the runtime's ownership check.
    with monkeypatch.context() as identity:
        identity.setattr(os, "geteuid", lambda: 0)
        with pytest.raises(ConfigError, match="secret_file_permissions"):
            runtime.require_generated_lab()
    assert runtime.require_generated_lab()["isolated_lab"] is True
    assert stat.S_IMODE((private / "bootstrap.json").stat().st_mode) == 0o600


@pytest.mark.parametrize("exit_code,failed,skipped", [(1, False, False), (0, True, False), (0, False, True)])
def test_failure_skip_and_nonzero_exit_cannot_be_pass(tmp_path, exit_code, failed, skipped):
    sample_result(tmp_path, failed=failed, skipped=skipped)
    result = runner.validate_result(tmp_path, exit_code)
    assert result["status"] == "FAIL"
    assert "private" not in json.dumps(result)


def test_report_allowlist_excludes_secret_fields_and_tracebacks(tmp_path):
    sample_result(tmp_path)
    result = runner.validate_result(tmp_path, 0)
    assert result["status"] == "PASS" and result["counts"]["passed"] == 4
    assert "do-not-export" not in json.dumps(result)


def test_unrelated_test_cannot_substitute_required_service_evidence(tmp_path):
    sample_result(tmp_path, extra=True)
    with pytest.raises(ValueError, match="selection"):
        runner.validate_result(tmp_path, 0)


def test_false_check_and_wrong_version_fail_closed(tmp_path):
    sample_result(tmp_path)
    report_path = tmp_path / "observations.json"
    data = json.loads(report_path.read_text())
    data["checks"]["authenticated_capture"] = False
    report_path.write_text(json.dumps(data))
    assert runner.validate_result(tmp_path, 0)["status"] == "FAIL"
    data["versions"]["zabbix"] = "7.0.99"
    report_path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="version"):
        runner.validate_result(tmp_path, 0)


def test_service_module_refuses_marker_before_fixture_reads(monkeypatch):
    monkeypatch.delenv("ACK_DEPLOYMENT_E2E", raising=False)
    from lab.deployment_e2e_runtime import require_generated_lab
    with pytest.raises(RuntimeError, match="isolated_service_lab_required"):
        require_generated_lab()


def test_pinned_alerta_selects_postgres_from_generated_url():
    from types import SimpleNamespace
    from urllib.parse import unquote, urlsplit
    from alerta.database.base import get_backend
    from lab.deployment_e2e_runtime import alerta_database_url
    password = "synthetic:@ /?&password"
    url = alerta_database_url(password)
    assert get_backend(SimpleNamespace(config={"DATABASE_URL": url})) == "postgres"
    parts = urlsplit(url)
    assert parts.hostname == "postgres" and parts.path == "/alerta"
    assert unquote(parts.password) == password


def test_one_off_runner_cleanup_checks_name_and_project_before_stop(monkeypatch):
    identity = "a" * 64
    calls = []
    def read(command, **kwargs):
        if command[1] == "ps":
            return identity + "\n"
        return json.dumps([{"Name": "/zabalack-e2e-0123456789ab-runner", "Config": {"Labels": {
            "com.docker.compose.project": "zabalack-e2e-0123456789ab", "com.docker.compose.service": "runner"}}}])
    monkeypatch.setattr(runner.subprocess, "check_output", read)
    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kwargs: calls.append(command))
    runner.stop_runner("zabalack-e2e-0123456789ab", "zabalack-e2e-0123456789ab-runner", {})
    assert calls == [["docker", "stop", "--time", "10", identity]]


def test_cleanup_refuses_other_project_even_if_name_matches(monkeypatch):
    identity = "a" * 64
    def read(command, **kwargs):
        return identity if command[1] == "ps" else json.dumps([{"Name": "/zabalack-e2e-0123456789ab-runner", "Config": {"Labels": {
            "com.docker.compose.project": "unrelated", "com.docker.compose.service": "runner"}}}])
    monkeypatch.setattr(runner.subprocess, "check_output", read)
    monkeypatch.setattr(runner.subprocess, "run", lambda *_, **__: pytest.fail("unrelated container stopped"))
    with pytest.raises(ValueError, match="identity"):
        runner.stop_runner("zabalack-e2e-0123456789ab", "zabalack-e2e-0123456789ab-runner", {})


@pytest.mark.parametrize("outcome", ["pass", "source_changed", "timeout", "interrupt"])
def test_owned_execution_cleanup_and_source_binding(tmp_path, monkeypatch, outcome):
    from types import SimpleNamespace
    import subprocess
    (tmp_path / "src").mkdir()
    (tmp_path / "src/product.py").write_text("baseline\n")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "local_docker", lambda *_: None)
    monkeypatch.setattr(runner, "certificates", lambda path: (path / "ca").mkdir())
    monkeypatch.setattr(runner, "staged_context", lambda path: path.mkdir())
    seen = []
    active = {}
    def execute(command, **kwargs):
        seen.append(command)
        if "compose" in command and "run" in command:
            private = Path(command[command.index("-f") + 1]).parent
            active["project"] = private.name
            active["name"] = command[command.index("--name") + 1]
            if outcome == "timeout":
                raise subprocess.TimeoutExpired(command, 600)
            if outcome == "interrupt":
                raise KeyboardInterrupt
            sample_result(private)
            if outcome == "source_changed":
                (tmp_path / "src/product.py").write_text("changed after execution\n")
        return SimpleNamespace(returncode=0)
    def read(command, **kwargs):
        if command[1] == "image":
            return "sha256:" + "a" * 64
        if command[1] == "ps":
            return "b" * 64  # Deliberately still-present one-off, even after success.
        return json.dumps([{"Name": "/" + active["name"], "Config": {"Labels": {
            "com.docker.compose.project": active["project"], "com.docker.compose.service": "runner"}}}])
    monkeypatch.setattr(runner.subprocess, "run", execute)
    monkeypatch.setattr(runner.subprocess, "check_output", read)
    code = runner.main(["--isolated-lab"])
    report = json.loads((tmp_path / "evidence/deployment-e2e-run.json").read_text())
    assert code == (0 if outcome == "pass" else 1)
    assert report["status"] == ("PASS" if outcome == "pass" else "FAIL")
    assert report["runner_shutdown_exit_code"] == 0 and report["shutdown_exit_code"] == 0
    assert ["docker", "stop", "--time", "10", "b" * 64] in seen
    assert any("compose" in command and "stop" in command for command in seen)
    if outcome == "source_changed":
        assert report["tree_unchanged"] is False
    if outcome == "interrupt":
        assert report["reason"] == "lab_interrupted"
