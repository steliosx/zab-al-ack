"""Native wire login identities; trust-auth lab is not deployed SCRAM evidence."""
import psycopg
import pytest

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize('role', ['alerta_app', 'zabalack_worker', 'zabalack_probe', 'zabalack_operator'])
def test_T35_T38_native_session_user_is_restricted_login(pg_lab, role):
    if pg_lab['backend'] != 'native_postgres18':
        pytest.skip('NOT_RUN_NATIVE: single-backend adapter cannot prove actual login identity')
    parameters = psycopg.conninfo.conninfo_to_dict(pg_lab['admin_dsn'])
    parameters.pop('options', None)
    parameters.pop('password', None)
    parameters.update(user=role, connect_timeout='3')
    with psycopg.connect(**parameters, autocommit=True) as connection:
        assert connection.execute('SELECT session_user,current_user').fetchone() == (role, role)
        permissions = connection.execute(
            'SELECT rolsuper,rolcreatedb,rolcreaterole,rolinherit FROM pg_roles WHERE rolname=current_user').fetchone()
        assert permissions == (False, False, False, False)
        if role == 'zabalack_probe':
            assert isinstance(connection.execute('SELECT zabalack.capture_health()').fetchone()[0], dict)
        if role in {'zabalack_probe', 'zabalack_worker'}:
            for statement in ['SELECT * FROM zabalack.captures LIMIT 1', "UPDATE public.alerts SET status='ack' WHERE false"]:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    connection.execute(statement)
