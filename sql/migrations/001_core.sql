-- Run as the non-login zabalack_owner authority. No Alerta trigger administration here.
DO $$ BEGIN
 IF pg_catalog.to_regnamespace('ackline') IS NOT NULL THEN
  RAISE EXCEPTION 'legacy_ackline_schema_present: fresh database required; see docs/RENAME.md';
 END IF;
 IF current_user <> 'zabalack_owner' THEN RAISE EXCEPTION 'migration requires current_user=zabalack_owner'; END IF;
 IF current_setting('server_version_num')::int < 180000 THEN RAISE EXCEPTION 'PostgreSQL 18 or newer reference required'; END IF;
 IF to_regclass('public.alerts') IS NULL OR to_regtype('public.history') IS NULL THEN RAISE EXCEPTION 'upstream Alerta alerts and history[] required'; END IF;
END $$;
CREATE TABLE zabalack.installation (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 deployment_id text NOT NULL DEFAULT 'REQUIRED_FOR_DEPLOYMENT', schema_version integer NOT NULL DEFAULT 1,
 config_revision text NOT NULL DEFAULT 'UNCONFIGURED', capture_disabled boolean NOT NULL DEFAULT false,
 external_writes_enabled boolean NOT NULL DEFAULT false, installed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 verification_turn bigint NOT NULL DEFAULT 0
);
INSERT INTO zabalack.installation(singleton) VALUES(true);
CREATE TABLE zabalack.cohorts (
 customer text NOT NULL, instance text NOT NULL, trigger_id text NOT NULL CHECK(trigger_id ~ '^[1-9][0-9]{0,19}$'),
 revision text NOT NULL, policy_version text NOT NULL, host_ids jsonb NOT NULL CHECK(jsonb_typeof(host_ids)='array'),
 group_ids jsonb NOT NULL CHECK(jsonb_typeof(group_ids)='array'), enabled boolean NOT NULL DEFAULT false,
 allow_comment boolean NOT NULL DEFAULT true, admin_exception boolean NOT NULL DEFAULT false,
 host_drift boolean NOT NULL DEFAULT false, host_checked_at timestamptz, host_cursor integer NOT NULL DEFAULT 0,
 PRIMARY KEY(customer,instance,trigger_id)
);
CREATE TABLE zabalack.instance_control (
 instance text PRIMARY KEY, paused boolean NOT NULL DEFAULT true, owner_ref text, change_ref text, reason text,
 expected_end timestamptz, changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 delivery_slots integer NOT NULL DEFAULT 2 CHECK(delivery_slots BETWEEN 1 AND 4),
 background_owner text, background_generation bigint NOT NULL DEFAULT 0, background_until timestamptz,
 circuit_open boolean NOT NULL DEFAULT false, circuit_reason text, next_background_at timestamptz NOT NULL DEFAULT '-infinity'
);
CREATE TABLE zabalack.captures (
 capture_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), capture_order bigint GENERATED ALWAYS AS IDENTITY UNIQUE, captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 alert_id text NOT NULL, customer text NOT NULL, snapshot jsonb NOT NULL, intent jsonb, marker text,
 history_type text NOT NULL, history_user text, history_update_time timestamptz, resulting_status text NOT NULL,
 diagnostic_reason text, state text NOT NULL DEFAULT 'captured_unverified', verified_action_id uuid
);
CREATE TABLE zabalack.outbox (
 capture_id uuid PRIMARY KEY REFERENCES zabalack.captures, due_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 state text NOT NULL DEFAULT 'captured_unverified', reason text, verification_count integer NOT NULL DEFAULT 0
);
CREATE INDEX outbox_due ON zabalack.outbox(due_at,capture_id) WHERE state IN ('captured_unverified','verifier_hold');
CREATE TABLE zabalack.verification_leases (
 capture_id uuid PRIMARY KEY REFERENCES zabalack.captures, owner text NOT NULL, generation bigint NOT NULL DEFAULT 1,
 expires_at timestamptz NOT NULL
);
CREATE TABLE zabalack.effects (
 effect_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), instance text NOT NULL, event_id text NOT NULL,
 generation bigint NOT NULL, primary_action_id uuid NOT NULL,
 state text NOT NULL DEFAULT 'authorised', reason text, completion_evidence text, completed_at timestamptz,
 latest_action_time timestamptz NOT NULL, UNIQUE(instance,event_id,generation)
);
CREATE TABLE zabalack.actions (
 action_id uuid PRIMARY KEY, capture_id uuid NOT NULL REFERENCES zabalack.captures, capture_order bigint NOT NULL, effect_id uuid NOT NULL REFERENCES zabalack.effects,
 alert_id text NOT NULL, customer text NOT NULL, instance text NOT NULL, event_id text NOT NULL, trigger_id text NOT NULL,
 actor_ref text NOT NULL, cohort_revision text NOT NULL, policy_version text NOT NULL,
 issued_at timestamptz NOT NULL, execute_before timestamptz NOT NULL,
 public_reference text NOT NULL DEFAULT replace(gen_random_uuid()::text,'-','') UNIQUE,
 state text NOT NULL DEFAULT 'authorised', reason text, completion_evidence text,
 uncertain boolean NOT NULL DEFAULT false, attempts integer NOT NULL DEFAULT 0,
 next_due timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 projection_at timestamptz, projection_reason text, projection_due_at timestamptz DEFAULT clock_timestamp(),
 drift_due_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '1 day'
);
CREATE INDEX effects_open ON zabalack.effects(primary_action_id) WHERE completed_at IS NULL;
CREATE INDEX actions_expiry ON zabalack.actions(execute_before,action_id) WHERE NOT uncertain AND state IN ('authorised','pending','held','delivery_claimed','expired');
CREATE INDEX actions_due ON zabalack.actions(next_due,action_id) WHERE state IN ('authorised','pending','delivery_claimed','unknown','held');
CREATE INDEX actions_projection_due ON zabalack.actions(projection_due_at,action_id) WHERE projection_due_at IS NOT NULL;
CREATE INDEX actions_drift_due ON zabalack.actions(drift_due_at,action_id) WHERE state='ok';
CREATE INDEX actions_source ON zabalack.actions(instance,event_id,issued_at DESC);
CREATE TABLE zabalack.actor_identity (
 actor_ref text PRIMARY KEY, login text NOT NULL, authenticated_user_id text, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE zabalack.delivery_leases (
 instance text NOT NULL, event_id text NOT NULL, action_id uuid NOT NULL REFERENCES zabalack.actions,
 owner text NOT NULL, generation bigint NOT NULL, expires_at timestamptz NOT NULL,
 PRIMARY KEY(instance,event_id)
);
CREATE TABLE zabalack.delivery_slots (
 instance text NOT NULL, slot integer NOT NULL, action_id uuid NOT NULL REFERENCES zabalack.actions,
 owner text NOT NULL, generation bigint NOT NULL, expires_at timestamptz NOT NULL,
 PRIMARY KEY(instance,slot), UNIQUE(action_id)
);
CREATE TABLE zabalack.attempts (
 attempt_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), action_id uuid NOT NULL REFERENCES zabalack.actions,
 owner text NOT NULL, lease_generation bigint NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 operation text NOT NULL CHECK(operation IN ('write','reconcile')),
 public_reference text NOT NULL, frozen_event_id text NOT NULL
);
CREATE TABLE zabalack.outcomes (
 outcome_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, action_id uuid NOT NULL REFERENCES zabalack.actions,
 attempt_id uuid REFERENCES zabalack.attempts, state text NOT NULL, reason text,
 completion_evidence text, uncertain boolean NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE zabalack.audit (
 audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, operation text NOT NULL,
 actor_ref text NOT NULL, change_ref text, object_ref text, details jsonb NOT NULL DEFAULT '{}',
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE zabalack.verifier_health (
 worker_id text PRIMARY KEY, last_seen timestamptz NOT NULL DEFAULT clock_timestamp(),
 healthy boolean NOT NULL, reason text
);
CREATE TABLE zabalack.incidents (
 instance text NOT NULL, failure_class text NOT NULL, opened_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 last_seen timestamptz NOT NULL DEFAULT clock_timestamp(), active boolean NOT NULL DEFAULT true,
 PRIMARY KEY(instance,failure_class)
);
REVOKE ALL ON ALL TABLES IN SCHEMA zabalack FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA zabalack FROM PUBLIC;
