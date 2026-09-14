#!/usr/bin/env python3
"""Deterministically build Zabbix JSON export; never import or send it."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
parameters = {
    "external_writes_enabled": "false", "alerta_api_url": "", "alerta_api_key": "",
    "alerta_customer": "", "zabbix_instance": "", "alerta_resource": "{HOST.HOST}",
    "alerta_event": "{TRIGGER.ID}", "alerta_environment": "", "alerta_severity": "warning",
    "event_id": "{EVENT.ID}", "trigger_id": "{TRIGGER.ID}", "event_value": "{EVENT.VALUE}",
    "event_recovery_id": "{EVENT.RECOVERY.ID}", "event_cause_id": "{EVENT.CAUSE.ID}",
    "event_update_status": "{EVENT.UPDATE.STATUS}", "event_update_message": "{EVENT.UPDATE.MESSAGE}",
}
export = {"zabbix_export": {"version": "7.0", "media_types": [{
    "name": "Zab-al-ack source contract v1 REFERENCE DISABLED", "type": "WEBHOOK", "status": "DISABLED",
    "parameters": [{"name": k, "value": v} for k, v in sorted(parameters.items())],
    "script": (ROOT / "webhook.js").read_text(), "timeout": "10s",
    "process_tags": "NO", "show_event_menu": "NO",
    "description": "Reference only. No live export supplied; not a validated drop-in. Preserve existing event/resource/correlation mappings. G1-G8 remain open. No endpoint, key, customer or logical instance is configured. Review every matching update action before enabling.",
    "message_templates": [
        {"event_source": "TRIGGERS", "operation_mode": "PROBLEM", "subject": "Reference source fact", "message": "Reference source fact"},
        {"event_source": "TRIGGERS", "operation_mode": "RECOVERY", "subject": "Reference recovery fact", "message": "Reference recovery fact"},
        {"event_source": "TRIGGERS", "operation_mode": "UPDATE", "subject": "Reference update fact", "message": "Reference update fact"}
    ]
}]}}
(ROOT / "media_type.reference.json").write_text(json.dumps(export, indent=2, sort_keys=True) + "\n")
