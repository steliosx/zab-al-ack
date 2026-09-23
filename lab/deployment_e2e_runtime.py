"""Service lab orchestrator: real installed entry points, auth and native logins.

This is synthetic provisioning, never an installed-environment collector. Runtime
connections are ordinary password logins; only migration provisioning selects its
explicit non-login owner role. No Flask test client, injected actor, storage facade
or injected health probe appears in this path.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import importlib.resources
from importlib.metadata import distribution, version
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

import httpx
import psycopg
from psycopg import sql

from lab.zabbix_reference import VERSION as ZABBIX_REFERENCE_VERSION
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

PRIVATE = Path("/run/zabalack-e2e")
ROOT = Path("/opt/zabalack-lab")
APP_URL = "https://127.0.0.1:58081"
SOURCE_URL = "http://zabbix-web:8080/api_jsonrpc.php"
CUSTOMER = "zabalack-lab"
INSTANCE = "deployment-lab"
REVISION = "deployment-e2e-r1"


def alerta_database_url(password):
    # Alerta selects its backend from the URL scheme before passing to Psycopg2.
    # The worker/probe separately accept ordinary libpq keyword DSNs.
    return "postgresql://alerta_app:" + quote(password, safe="") + "@postgres:5432/alerta"


def require_generated_lab():
    if os.environ.get("ACK_DEPLOYMENT_E2E") != "GENERATED_ISOLATED_PROJECT":
        raise RuntimeError("isolated_service_lab_required")
    from zabalack.config import read_protected_file
    data = json.loads(read_protected_file(str(PRIVATE / "bootstrap.json")))
    if data.get("isolated_lab") is not True or not re.fullmatch(r"zabalack-e2e-[0-9a-f]{12}", data.get("project", "")):
        raise RuntimeError("generated_lab_marker_required")
    if set(data.get("passwords", {})) != {"alerta_app", "zabalack_worker", "zabalack_probe", "zabalack_operator", "migrator", "human", "ingest", "outside", "admin"}:
        raise RuntimeError("generated_password_shape")
    return data


def secret(name, value):
    path = PRIVATE / name
    path.write_text(value)
    path.chmod(0o600)
    return str(path)


def wait_until(operation, *, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = operation()
        if result:
            return result
        time.sleep(0.2)
    raise RuntimeError("bounded_service_observation_timeout")


def wait_for_projection(admin, identity, event_id, worker):
    """Ledger completion and projection commit separately; require the full view."""
    def projected():
        if worker.poll() is not None:
            raise RuntimeError("worker_process_exited")
        state = admin.execute("SELECT attributes,status,(history[1]).type FROM alerts WHERE id=%s", (identity,)).fetchone()
        if (state and state[0].get("zabbix_ack_sync") == "ok"
                and state[0].get("zabbix_event_id") == event_id and state[1:] == ("ack", "ack")):
            return state
        return None
    return wait_until(projected)


@contextmanager
def child(command, name, env=None):
    # Explicit small child environment prevents inherited app/proxy config.
    # PYTHONDONTWRITEBYTECODE: the supplied-wheel check refuses bytecode in the installed package.
    environment = {"PATH": os.environ["PATH"], "TZ": "UTC", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/tmp"}
    environment.update(env or {})
    with (PRIVATE / (name + ".log")).open("w") as output:
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=output, stderr=subprocess.STDOUT)
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


@contextmanager
def source_tls_proxy():
    """Fixed internal target; production client verifies the generated lab cert."""
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/api_jsonrpc.php":
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1048576:
                    raise ValueError
                body = self.rfile.read(size)
                with httpx.Client(trust_env=False, follow_redirects=False, timeout=5) as client:
                    response = client.post(SOURCE_URL, content=body, headers={"Content-Type": "application/json",
                                           "Authorization": self.headers.get("Authorization", "")})
                self.send_response(response.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response.content)))
                self.end_headers()
                self.wfile.write(response.content)
            except (ValueError, OSError, httpx.HTTPError):
                self.send_error(502)

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 58443), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(PRIVATE / "server.crt", PRIVATE / "server.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def provision_database(passwords):
    admin_dsn = make_conninfo(host="postgres", port=5432, dbname="alerta", user="lab_admin", password=(PRIVATE / "pg-password").read_text())
    dsns = {name: make_conninfo(host="postgres", port=5432, dbname="alerta", user=name, password=passwords[name], connect_timeout=3)
            for name in ("alerta_app", "zabalack_worker", "zabalack_probe", "zabalack_operator", "migrator")}
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        if admin.execute("SELECT current_database(),current_user,to_regclass('public.alerts'),to_regnamespace('zabalack')").fetchone() != ("alerta", "lab_admin", None, None):
            raise RuntimeError("fresh_database_required")
        admin.execute(sql.SQL("CREATE ROLE alerta_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD {}").format(sql.Literal(passwords["alerta_app"])))
        admin.execute("GRANT CREATE, USAGE ON SCHEMA public TO alerta_app")
        admin.execute("GRANT CONNECT ON DATABASE alerta TO alerta_app")
    # Actual pinned upstream schema under its dedicated table-owner login.
    with psycopg.connect(dsns["alerta_app"], autocommit=True) as app:
        schema = importlib.resources.files("alerta").joinpath("sql/schema.sql").read_text()
        app.execute(schema)
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        roles = (ROOT / "sql/install/00_roles.sql").read_text()
        admin.execute(roles.replace(':"database_name"', '"alerta"').replace(':"alert_owner"', '"alerta_app"'))
        for role in ("zabalack_worker", "zabalack_probe", "zabalack_operator"):
            admin.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(passwords[role])))
        admin.execute(sql.SQL("CREATE ROLE migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD {}").format(sql.Literal(passwords["migrator"])))
        admin.execute("GRANT zabalack_owner TO migrator")
    # Shipped versioned migration runner, isolated installer login, explicit role.
    with (PRIVATE / "migration.log").open("w") as log:
        subprocess.run([sys.executable, "sql/migrate.py", "--set-role", "zabalack_owner"], cwd=ROOT,
                       env={"PATH": os.environ["PATH"], "TZ": "UTC", "ZABALACK_MIGRATION_DSN": dsns["migrator"]},
                       check=True, timeout=60, stdout=log, stderr=subprocess.STDOUT)
    # The application role's single entry point into zabalack, granted by the
    # schema owner. psql would substitute the variable; the runtime splices it.
    with psycopg.connect(dsns["migrator"], autocommit=True) as migrator:
        migrator.execute("SET ROLE zabalack_owner")
        migrator.execute((ROOT / "sql/install/09_installer_roles.sql").read_text()
                         .replace(":'installer_roles'", "'migrator'"))
        migrator.execute((ROOT / "sql/install/12_application_grant.sql").read_text()
                         .replace(':"alert_app_role"', '"alerta_app"'))
    return admin_dsn, dsns


def configure(dsns, source):
    cohort = {"customer": CUSTOMER, "zabbix_instance": INSTANCE, "zabbix_trigger_id": source.trigger_id,
        "host_ids": [source.host_id], "group_ids": [source.group_id], "revision": REVISION,
        "policy_revision": "1", "enabled": True, "one_to_one": True, "comment_enabled": True, "allow_admin": False}
    config = {"enabled": True, "external_writes_enabled": True, "config_revision": REVISION,
        "deployment_id": "isolated-deployment-e2e", "database_name": "alerta", "signing_kid": "lab",
        "signing_keys": {"lab": secret("signing.key", secrets.token_hex(32))}, "cohorts": [cohort],
        "probe_dsn_file": secret("probe.dsn", dsns["zabalack_probe"]),
        "worker_dsn_file": secret("worker.dsn", dsns["zabalack_worker"]),
        "operator_dsn_file": secret("operator.dsn", dsns["zabalack_operator"]),
        "approved_versions": [ZABBIX_REFERENCE_VERSION], "lab_mode": True,
        "ingestion_mappings": {"ingest@lab.invalid": {"customer": CUSTOMER, "instance": INSTANCE}},
        "endpoint_registry": {INSTANCE: {"logical_instance": INSTANCE, "url": "https://127.0.0.1:58443/api_jsonrpc.php",
            "token_file": secret("source.token", source.worker_token), "ca_file": str(PRIVATE / "server.crt"), "api_version": ZABBIX_REFERENCE_VERSION}}}
    path = secret("zabalack.json", json.dumps(config))
    with psycopg.connect(dsns["zabalack_operator"], autocommit=True) as operator:
        audit = {"actor_ref": "synthetic-deployment-operator", "change_ref": "ISOLATED-LAB-ONLY"}
        def control(operation, **payload):
            operator.execute("SELECT zabalack.operator_call(%s,%s)", (operation, Jsonb(audit | payload))).fetchone()
        control("configure_installation", deployment_id=config["deployment_id"], config_revision=REVISION)
        control("configure_cohort", cohort=cohort)
        control("pause", instance=INSTANCE)
    with psycopg.connect(dsns["alerta_app"], autocommit=True) as installer:
        installer.execute((ROOT / "sql/install/10_trigger.sql").read_text())
    # Installed operational CLI controls; explicit synthetic acknowledgement only.
    for args in (["enable-delivery", "--gates-reviewed", "--confirmation", "ENABLE_EXTERNAL_WRITES"], ["resume"]):
        with (PRIVATE / "controls.log").open("a") as output:
            subprocess.run(["zabalack", "--config", path, *args, "--instance", INSTANCE,
                            "--owner", "synthetic-lab-operator", "--change-ref", "ISOLATED-LAB-ONLY"],
                           stdout=output, stderr=subprocess.STDOUT, check=True, timeout=15)
    return config, path


def normal_auth(client, passwords):
    def request(method, path, body, token=None):
        headers = {"Authorization": "Bearer " + token} if token else {}
        response = client.request(method, APP_URL + path, json=body, headers=headers)
        if response.status_code not in (200, 201):
            raise RuntimeError("normal_auth_bootstrap_failed")
        return response.json()
    admin = request("POST", "/auth/signup", {"name": "Synthetic admin", "email": "admin@lab.invalid", "password": passwords["admin"]})["token"]
    tokens = {}
    for name, customer in (("human", CUSTOMER), ("outside", "outside-lab")):
        email = name + "@lab.invalid"
        request("POST", "/customer", {"match": email, "customer": customer}, admin)
        request("POST", "/auth/signup", {"name": "Synthetic " + name, "email": email, "password": passwords[name]})
        tokens[name] = request("POST", "/auth/login", {"username": email, "password": passwords[name]})["token"]
    request("POST", "/customer", {"match": "ingest@lab.invalid", "customer": CUSTOMER}, admin)
    key = request("POST", "/key", {"user": "ingest@lab.invalid", "customer": CUSTOMER, "scopes": ["write:alerts"], "text": "Synthetic isolated media ingestion"}, admin)["key"]
    return tokens, key


def execute_service_path():
    data = require_generated_lab()
    passwords = data["passwords"]
    from lab.deployment_e2e_zabbix import ZabbixLab
    checks = {}
    with ExitStack() as stack:
        zabbix = ZabbixLab(isolated_lab=True, fresh_instance=True)
        stack.callback(zabbix.close)
        source = zabbix.bootstrap()
        admin_dsn, dsns = provision_database(passwords)
        config, config_path = configure(dsns, source)
        # Current upstream factory needs its table owner even after preinstallation.
        app_conf = {"DATABASE_URL": alerta_database_url(passwords["alerta_app"]), "DATABASE_NAME": None, "DATABASE_SCHEMA": "public",
            "AUTH_REQUIRED": True, "AUTH_PROVIDER": "basic", "CUSTOMER_VIEWS": True,
            "ADMIN_USERS": ["admin@lab.invalid"], "USER_ROLES": ["user"], "ADMIN_ROLES": ["admin"],
            "ALLOWED_EMAIL_DOMAINS": ["lab.invalid"], "SIGNUP_ENABLED": True, "EMAIL_VERIFICATION": False,
            "SECRET_KEY": secrets.token_hex(32), "OAUTH2_CLIENT_ID": "isolated-deployment-e2e",
            "PLUGINS": ["zabalack_zabbix_normalise", "zabalack_zabbix_ack"], "PLUGINS_RAISE_ON_ERROR": True,
            "ZABALACK_CONFIG_FILE": config_path, "AUDIT_TRAIL": [], "DEBUG": False}
        conf_path = secret("alertad.conf", "\n".join(key + " = " + repr(value) for key, value in app_conf.items()) + "\n")
        app = stack.enter_context(child([sys.executable, "-m", "flask", "--app", "zabalack.plugins:create_app()", "run",
            "--host", "0.0.0.0", "--port", "58081", "--cert", str(PRIVATE / "server.crt"), "--key", str(PRIVATE / "server.key"), "--no-reload"],
            "alerta", {"ALERTA_SVR_CONF_FILE": conf_path}))
        context = ssl.create_default_context(cafile=str(PRIVATE / "server.crt"))
        client = stack.enter_context(httpx.Client(verify=context, trust_env=False, follow_redirects=False, timeout=5))
        def ready():
            if app.poll() is not None:
                raise RuntimeError("alerta_process_exited")
            try:
                return client.get(APP_URL + "/management/healthcheck").status_code == 200
            except httpx.HTTPError:
                return False
        wait_until(ready)
        tokens, ingest_key = normal_auth(client, passwords)
        zabbix.enable_media(ingest_key, customer=CUSTOMER, instance=INSTANCE)
        event_id = zabbix.raise_problem()
        zabbix.wait_delivery(event_id, minimum_count=1)
        human = {"Authorization": "Bearer " + tokens["human"]}
        def ingested():
            response = client.get(APP_URL + "/alerts", headers=human)
            if response.status_code != 200:
                raise RuntimeError("authenticated_alert_read_failed")
            alerts = response.json().get("alerts", [])
            return next((alert for alert in alerts if alert.get("attributes", {}).get("zabbix_event_id") == event_id), None)
        alert = wait_until(ingested)
        identity = alert["id"]
        attrs = alert["attributes"]
        checks["media_macro_ingestion"] = attrs.get("zabbix_trigger_id") == source.trigger_id and attrs.get("zabbix_instance") == INSTANCE and alert.get("customer") == CUSTOMER
        duplicate_before = alert["duplicateCount"]
        # A real other-tenant JWT must not read or acknowledge the pilot alert.
        outside = {"Authorization": "Bearer " + tokens["outside"]}
        denied_read = client.get(APP_URL + "/alert/" + identity, headers=outside)
        denied_ack = client.put(APP_URL + "/alert/" + identity + "/action", headers=outside, json={"action": "ack"})
        checks["cross_customer_denied"] = denied_read.status_code in (403, 404) and denied_ack.status_code in (403, 404)
        stack.enter_context(source_tls_proxy())
        worker = stack.enter_context(child(["zabalack", "--config", config_path, "worker"], "worker"))
        response = client.put(APP_URL + "/alert/" + identity + "/action", headers=human, json={"action": "ack", "text": "Synthetic deployment acknowledgement"})
        if response.status_code != 200:
            raise RuntimeError("human_action_failed")
        admin = stack.enter_context(psycopg.connect(admin_dsn, autocommit=True))
        def completed():
            if worker.poll() is not None:
                raise RuntimeError("worker_process_exited")
            return admin.execute("SELECT state FROM zabalack.actions WHERE alert_id=%s", (identity,)).fetchone() == ("ok",)
        wait_until(completed)
        capture = admin.execute("SELECT count(*),min(history_user) FROM zabalack.captures WHERE alert_id=%s", (identity,)).fetchone()
        checks["authenticated_capture"] = capture == (1, "human@lab.invalid")
        state = wait_for_projection(admin, identity, event_id, worker)
        checks["projection"] = state[0].get("zabbix_ack_sync") == "ok" and state[0].get("zabbix_event_id") == event_id and state[1:] == ("ack", "ack")
        event = zabbix.event(event_id)
        comments = [entry for entry in event["acknowledges"] if entry.get("message", "").startswith("[zabalack]")]
        writes = admin.execute("SELECT count(*) FROM zabalack.attempts t JOIN zabalack.actions a USING(action_id) WHERE a.alert_id=%s AND t.operation='write'", (identity,)).fetchone()[0]
        checks["exactly_one_effect"] = str(event["acknowledged"]) == "1" and len(comments) == 1 and writes == 1
        checks["repeat_409"] = client.put(APP_URL + "/alert/" + identity + "/action", headers=human, json={"action": "ack"}).status_code == 409
        # Require the actual Zabbix update operation completed; mere silence is insufficient.
        zabbix.wait_delivery(event_id, minimum_count=2, update=True)
        checks["neutral_update_filtered"] = ingested()["duplicateCount"] == duplicate_before
        zabbix.human_update(event_id)
        zabbix.wait_delivery(event_id, minimum_count=3, update=True)
        checks["human_update_recorded"] = bool(wait_until(lambda: ingested()["duplicateCount"] > duplicate_before))
        recovery_id = zabbix.recover(event_id)
        recovered = wait_until(lambda: (value if (value := ingested())["attributes"].get("zabbix_recovery_event_id") == recovery_id else None))
        checks["recovery_macro_ingestion"] = recovered["attributes"]["zabbix_event_id"] == event_id and recovered["severity"] == "normal"
        # A separate alert uses the same real source, but shared ingestion keys stay local-only.
        producer = {"resource": "synthetic-api-key-policy", "event": "synthetic-stable-family", "environment": "Production", "severity": "major",
                    "customer": CUSTOMER, "attributes": {key: value for key, value in attrs.items() if not key.startswith("zabbix_ack_sync")}}
        key_headers = {"Authorization": "Key " + ingest_key}
        created = client.post(APP_URL + "/alert", headers=key_headers, json=producer)
        if created.status_code not in (200, 201, 202):
            raise RuntimeError("ingestion_key_create_failed")
        key_id = created.json()["id"]
        key_ack = client.put(APP_URL + "/alert/" + key_id + "/action", headers=key_headers, json={"action": "ack"})
        key_captures = admin.execute("SELECT count(*) FROM zabalack.captures WHERE alert_id=%s", (key_id,)).fetchone()[0]
        checks["ingest_key_local_only"] = key_ack.status_code == 200 and key_captures == 0
        identities = []
        for role in ("alerta_app", "zabalack_worker", "zabalack_probe", "zabalack_operator"):
            with psycopg.connect(dsns[role], autocommit=True) as connection:
                identities.append(connection.execute("SELECT session_user,current_user").fetchone() == (role, role))
                if role in ("zabalack_worker", "zabalack_probe"):
                    query = "UPDATE public.alerts SET status='closed' WHERE id=%s" if role == "zabalack_worker" else "SELECT attributes FROM public.alerts WHERE id=%s"
                    try:
                        connection.execute(query, (identity,))
                    except psycopg.errors.InsufficientPrivilege:
                        checks["worker_denied_table_update" if role == "zabalack_worker" else "probe_denied_table_read"] = True
                    else:
                        checks["worker_denied_table_update" if role == "zabalack_worker" else "probe_denied_table_read"] = False
        checks["separate_native_logins"] = all(identities)
        postgres_major = str(int(admin.execute("SHOW server_version_num").fetchone()[0]) // 10000)
        versions = {"alerta": version("alerta-server"), "zabbix": ZABBIX_REFERENCE_VERSION, "postgres_major": postgres_major}
        # Verify the installed distribution has both real plugin entry points.
        entrypoints = {entry.name for entry in distribution("zabalack").entry_points if entry.group == "alerta.plugins"}
        if entrypoints != {"zabalack_zabbix_normalise", "zabalack_zabbix_ack"}:
            raise RuntimeError("installed_plugin_entrypoints_missing")
        result = {"checks": checks, "versions": versions}
        secret("observations.json", json.dumps(result))
        return result
