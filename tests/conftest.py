"""Disposable native PostgreSQL fixture. Absence is explicit environment blockage."""
import importlib.util
import os
import sys
from pathlib import Path
import pytest
import psycopg
from psycopg import sql

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope='session')
def pg_lab():
    dsn=os.environ.get('ACK_TEST_ADMIN_DSN')
    if not dsn:
        pytest.skip('BLOCKED_ENVIRONMENT: ACK_TEST_ADMIN_DSN for disposable native PostgreSQL18 not supplied')
    if os.environ.get('ACK_TEST_DESTRUCTIVE_LAB')!='YES_ISOLATED_LAB':
        pytest.fail('Explicit ACK_TEST_DESTRUCTIVE_LAB=YES_ISOLATED_LAB required for isolated role/schema setup')
    source=Path(os.environ['ACK_TEST_ALERTA_SCHEMA']) if os.environ.get('ACK_TEST_ALERTA_SCHEMA') else (Path(os.environ['ACK_TEST_ALERTA_SOURCE'])/'alerta/sql/schema.sql' if os.environ.get('ACK_TEST_ALERTA_SOURCE') else ROOT/'lab/upstream/alerta-9.1.0-schema.sql')
    if not source.is_file(): pytest.fail('Pinned Alerta upstream schema missing')
    with psycopg.connect(dsn,autocommit=True) as admin:
        version=admin.execute('SELECT version()').fetchone()[0]
        is_wasm='pglite' in version.lower() or 'emscripten' in version.lower()
        if os.environ.get('ACK_TEST_WASM')=='1':
            if not is_wasm: pytest.fail('ACK_TEST_WASM requires observed PGlite engine')
            if not admin.execute("SELECT to_regnamespace('zabalack')").fetchone()[0]: pytest.fail('Supplemental WASM runner must prebootstrap schema')
            yield {'admin_dsn':dsn,'source':source,'backend':'postgres18_wasm','version':version,
                   'database_name':admin.execute('SELECT current_database()').fetchone()[0]}
            return
        if is_wasm: pytest.fail('PGlite must be labelled ACK_TEST_WASM=1, never native evidence')
        assert int(admin.execute('SHOW server_version_num').fetchone()[0])>=180000
        if admin.execute("SELECT to_regnamespace('zabalack')").fetchone()[0]:
            pytest.fail('zabalack schema already exists: use a fresh disposable database; fixture never deletes existing data')
        admin.execute((ROOT/'tests/postgres/bootstrap.sql').read_text())
        dbname=admin.execute('SELECT current_database()').fetchone()[0]
        admin.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO alerta_owner,alerta_app,zabalack_owner,zabalack_worker,zabalack_probe,zabalack_operator,zabalack_installer,alerta_app_next,alerta_app_nologin').format(sql.Identifier(dbname)))
        admin.execute('SET ROLE alerta_owner')
        admin.execute(source.read_text())
        admin.execute('RESET ROLE')
        admin.execute((ROOT/'tests/postgres/bootstrap_after_schema.sql').read_text())
        admin.execute('SET ROLE zabalack_owner')
        spec=importlib.util.spec_from_file_location('ack_sql_migration',ROOT/'sql/migrate.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.migrate(admin)
        admin.execute((ROOT/'sql/install/09_installer_roles.sql').read_text().replace(":'installer_roles'","''"))
        admin.execute((ROOT/'sql/install/12_application_grant.sql').read_text().replace(':"alert_app_role"','"alerta_app"'))
        admin.execute('RESET ROLE')
        admin.execute('SET ROLE alerta_owner')
        admin.execute((ROOT/'sql/install/10_trigger.sql').read_text())
        admin.execute('RESET ROLE')
    # database_name is the observed current_database(); worker settings in the
    # native tests must follow the lab DSN (Compose uses `alerta`, the local
    # runtime uses `postgres`) rather than hard-code either name.
    yield {'admin_dsn':dsn,'source':source,'backend':'native_postgres18','version':version,'database_name':dbname}

def connect_role(pg_lab,role):
    conn=psycopg.connect(pg_lab['admin_dsn'],autocommit=True)
    conn.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
    return conn

@pytest.fixture
def pgdb(pg_lab):
    """Role-selected real DB connection factory; role assertions use current_user."""
    return lambda role: connect_role(pg_lab,role)

@pytest.fixture(autouse=True)
def query_builder_not_inherited():
    """No test inherits another test's alert query builder.

    `configure_app` installs the reader restriction on the module global
    `alerta.app.qb.alerts` and refuses an absent one. That global outlives the
    test that built it, so a Flask fixture omitting `qb.init_app` would pass
    behind one that did and fail alone. Clearing it first makes the omission
    fail in any order. Absent for a run that never imports Alerta.
    """
    module=sys.modules.get('alerta.app')
    if module is not None:
        builder=module.qb
        # init_app assigns self.__class__.alerts, so the class attribute is the
        # one that outlives a test; an instance attribute would only shadow it.
        builder.__dict__.pop('alerts',None)
        type(builder).alerts=None
