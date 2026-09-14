-- Run as zabalack_operator with explicit psql values: instance, actor_ref, change_ref.
-- Preserves captures, ledger, leases, attempts and all source state.
SELECT zabalack.operator_call('pause',jsonb_build_object('instance',:'instance','actor_ref',:'actor_ref','change_ref',:'change_ref','reason','rollback_hold'));
SELECT zabalack.operator_call('set_external_writes',jsonb_build_object('enabled',false,'actor_ref',:'actor_ref','change_ref',:'change_ref'));
