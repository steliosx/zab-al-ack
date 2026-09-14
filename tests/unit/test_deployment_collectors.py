"""Safe tooling assertions; synthetic tests do not close deployment gates."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("inventory_collector", ROOT / "collectors/collect.py")
collector = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(collector)


def test_static_collector_does_not_execute_configuration(tmp_path):
    marker = tmp_path / "MUST_NOT_EXIST"
    config = tmp_path / "alertad.conf"
    config.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('unsafe')\nAUTH_REQUIRED=True\nCUSTOMER_VIEWS=True\nDATABASE_URL='postgres://person:secret@example/alerta'\nPLUGINS=['existing','zabalack_zabbix_ack']\nROUTING=calculate_routes()\nSECRET='forbidden-export'\n")
    result = collector.local_inventory(config)
    encoded = json.dumps(result)
    assert not marker.exists()
    assert "secret@example" not in encoded and "forbidden-export" not in encoded
    assert result['static_configuration']['AUTH_REQUIRED'] is True
    assert result['runtime_configuration_verified'] is False
    assert 'ROUTING' in result['unresolved_configuration']


def test_payload_redaction_drops_proofs_and_notes():
    clean = collector.redact_payload({'customer':'real-person', 'event':'private-name', 'text':'private-note',
       'attributes':{'zabbix_event_id':'123','zabbix_trigger_id':'234','source_system':'zabbix','source_contract_version':'1',
                     'zabbix_ack_sync_intent':{'actor':'private-person'},'password':'private-secret'}})
    encoded = json.dumps(clean)
    assert clean['attributes']['zabbix_event_id'] == '123'
    assert all(v not in encoded for v in ['private-person','private-secret','private-note','private-name','real-person'])


def test_rpc_collector_rejects_write_method_before_connection():
    import pytest
    with pytest.raises(ValueError, match='read-only'):
        collector.rpc_read('https://example.invalid/api_jsonrpc.php','unused','unused','event.acknowledge',{})


def test_zabbix_collector_parameters_contract_and_redaction(tmp_path, monkeypatch):
    from argparse import Namespace
    token=tmp_path/'token';token.write_text('synthetic_secret');token.chmod(0o600)
    calls=[]
    def read(endpoint, ca, secret, method, params):
        calls.append((method,params))
        if method=='apiinfo.version': return '7.0.0'
        assert method=='mediatype.get'
        assert 'selectParameters' not in params and 'parameters' in params['output']
        return [{'mediatypeid':'123','type':'4','status':'0','script':'private_script_secret',
                 'parameters':[{'name':'api_key','value':'private_token'},{'name':'event_id','value':'{EVENT.ID}'}]}]
    monkeypatch.setattr(collector,'rpc_read',read)
    result=collector.zabbix_inventory(Namespace(token_file=str(token),action_id=[],media_type_id=['123'],trigger_id=[],endpoint='https://example.invalid/api_jsonrpc.php',ca_file='synthetic.pem'))
    assert len(calls)==2
    assert all(v not in json.dumps(result) for v in ['private_token','private_script_secret','synthetic_secret'])
    assert result['media_types'][0]['parameters'][1]['value']=='{EVENT.ID}'


def test_zabbix_trigger_response_is_allowlisted(tmp_path, monkeypatch):
    from argparse import Namespace
    token=tmp_path/'token';token.write_text('synthetic_secret');token.chmod(0o600)
    def read(endpoint, ca, secret, method, params):
        if method=='apiinfo.version': return '7.0.0'
        assert method=='trigger.get'
        return [{'triggerid':'123','type':'0','secret':'private-secret','hosts':[{'hostid':'234','name':'private-name'}]}]
    monkeypatch.setattr(collector,'rpc_read',read)
    result=collector.zabbix_inventory(Namespace(token_file=str(token),action_id=[],media_type_id=[],trigger_id=['123'],endpoint='https://example.invalid/api_jsonrpc.php',ca_file='synthetic.pem'))
    assert result['triggers']==[{'triggerid':'123','type':'0','hosts':[{'hostid':'234'}]}]


def test_protected_secret_owner_is_checked(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pytest
    token=tmp_path/'token';token.write_text('synthetic_secret');token.chmod(0o600)
    actual=token.stat()
    with monkeypatch.context() as isolated:
        isolated.setattr(collector.os,'fstat',lambda fd:SimpleNamespace(st_mode=actual.st_mode,st_size=actual.st_size,st_uid=12345))
        with pytest.raises(ValueError): collector.read_file(token,protected=True)


def test_output_private_no_clobber(tmp_path):
    source = tmp_path / 'source.json'
    source.write_text('{"customer":"private"}')
    out = tmp_path / 'inventory.json'
    command=[sys.executable,str(ROOT/'collectors/collect.py'),'--output',str(out),'payload','--input',str(source)]
    assert subprocess.run(command, capture_output=True).returncode == 0
    assert out.stat().st_mode & 0o777 == 0o600
    assert json.loads(out.read_text())['deployment_gate_passed'] is False
    assert subprocess.run(command,capture_output=True).returncode == 2


def test_rollback_plan_does_not_execute_or_delete():
    result=subprocess.run([sys.executable,str(ROOT/'deploy/rollback.py'),'--config','/not-present/operator.json',
                           '--instance','synthetic-zabbix-1','--owner','synthetic-operator','--change-ref','synthetic-change'],
                          capture_output=True,text=True,check=True)
    plan=json.loads(result.stdout)
    assert plan['mode']=='plan_only'
    assert not plan['deletes_evidence'] and not plan['disables_trigger'] and not plan['unacknowledges_source']


def test_media_export_inert_and_reproducible():
    export=ROOT/'zabbix/v1/media_type.reference.json'
    original=export.read_bytes()
    subprocess.run([sys.executable,str(ROOT/'zabbix/v1/build_export.py')],check=True)
    assert original==export.read_bytes()
    data=json.loads(original)['zabbix_export']
    assert data['version']=='7.0'
    media=data['media_types'][0]
    params={p['name']:p['value'] for p in media['parameters']}
    assert media['status']=='DISABLED' and params['external_writes_enabled']=='false'
    assert all(params[k]=='' for k in ['alerta_api_url','alerta_api_key','alerta_customer','zabbix_instance'])


def test_synthetic_media_contract_and_early_echo():
    import shutil
    import pytest
    if not shutil.which('node'):
        pytest.skip('Node unavailable; JS fixtures not executed')
    result=subprocess.run(['node',str(ROOT/'zabbix/v1/test_webhook.cjs')],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)['passed']==15
