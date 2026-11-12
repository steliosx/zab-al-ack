"""The reference alert rules against what the exporter really emits.

Kept out of tests/unit/test_worker.py on purpose: that file is in the
mutation runner's test set, and the runner's tree copy holds only src, tests,
tools and config, so a test that reads zabbix/monitoring.rules.yml errors
there (it did, on the first version of this change).

These read the YAML and the regular expression only. They are not a promtool
evaluation and say nothing about an installed alert.
"""
import re

from zabalack.config import Settings


def _reference_rules():
    import yaml
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "zabbix" / "monitoring.rules.yml"
    return {rule["alert"]: rule for group in yaml.safe_load(path.read_text())["groups"] for rule in group["rules"]}


def test_every_exported_failure_class_is_matched_by_the_reference_alert_rule():
    """PromQL =~ is fully anchored: `credential` did not match
    `credential_expiry_metadata`, so that failure raised no alert."""
    from zabalack.metrics import prometheus_text
    settings = Settings(config_revision="v1", deployment_id="lab", endpoint_registry={
        "a": {"token_expires_at": 7}, "b": {}})
    text = prometheus_text({"verifier_unhealthy": 1, "active_incidents": [
        {"instance": "b", "failure_class": "api_permission_or_request_rejected"},
        {"instance": "b", "failure_class": "token_rejected"}]}, settings)
    exported = set(re.findall(r'zabalack_instance_failure\{failure_class="([^"]+)"', text))
    assert exported == {"credential", "credential_expiry_metadata", "permission", "capture_health", "verifier_key"}
    expr = _reference_rules()["AlertaZabbixAckCredentialOrPermission"]["expr"]
    pattern = re.search(r'failure_class=~"([^"]+)"', expr).group(1)
    assert {code for code in exported if re.fullmatch(pattern, code)} == exported


def test_the_reference_rules_read_only_metrics_the_exporter_emits():
    from zabalack.metrics import prometheus_text
    settings = Settings(config_revision="v1", deployment_id="lab", endpoint_registry={
        "a": {"token_expires_at": "2999-01-01T00:00:00Z"}})
    text = prometheus_text({"per_instance": [{"instance": "a", "oldest_pending_seconds": 1}],
        "active_incidents": [{"instance": "a", "failure_class": "token_rejected"}]}, settings)
    emitted = set(re.findall(r"^(zabalack_\w+)", text, re.MULTILINE))
    rules = _reference_rules()
    for rule in rules.values():
        used = set(re.findall(r"zabalack_\w+", rule["expr"]))
        assert used and used <= emitted, rule["alert"]
    expiring, expired = rules["AlertaZabbixAckCredentialExpiring"], rules["AlertaZabbixAckCredentialExpired"]
    assert expiring["expr"] == ("(min by (instance) (zabalack_credential_expiry_seconds) < 2592000)"
                                " and (min by (instance) (zabalack_credential_expiry_seconds) > 0)")
    assert expiring["labels"]["severity"] == "warning"
    assert expired["expr"].endswith("(zabalack_credential_expiry_seconds) <= 0") and expired["labels"]["severity"] == "critical"
