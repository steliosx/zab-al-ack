-- Forward correction: a no-effect settlement before TTL remains failed until
-- a separate retry, but must release its source-effect generation at expiry.
-- Preserve 001-006. No automatic retry, changed deadline, grants or resealing.
CREATE OR REPLACE FUNCTION zabalack.expire_due_effects(p_limit integer DEFAULT 200) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack AS $$
DECLARE expired_row record; affected integer:=0;
BEGIN
 FOR expired_row IN SELECT primary_action.action_id,primary_action.effect_id,primary_action.execute_before
  FROM zabalack.actions primary_action JOIN zabalack.effects effect ON effect.primary_action_id=primary_action.action_id
  WHERE effect.completed_at IS NULL AND primary_action.execute_before<=clock_timestamp() AND NOT primary_action.uncertain
   AND (primary_action.state IN ('authorised','pending','held','delivery_claimed','expired')
     OR (primary_action.state='failed' AND primary_action.reason='operator_no_effect_confirmed'))
   AND NOT EXISTS(SELECT 1 FROM zabalack.delivery_leases lease WHERE lease.action_id=primary_action.action_id AND lease.expires_at>clock_timestamp())
   AND NOT EXISTS(SELECT 1 FROM zabalack.attempts attempt WHERE attempt.action_id=primary_action.action_id AND attempt.operation='write'
      AND zabalack.attempt_unsettled(attempt.attempt_id))
  ORDER BY primary_action.execute_before LIMIT least(200,greatest(1,p_limit)) FOR UPDATE OF primary_action SKIP LOCKED
 LOOP
  UPDATE zabalack.effects SET state='expired',reason='execution_lifetime_expired',completed_at=expired_row.execute_before WHERE effect_id=expired_row.effect_id;
  UPDATE zabalack.actions SET state='expired',reason='execution_lifetime_expired',updated_at=clock_timestamp(),projection_due_at=clock_timestamp()
   WHERE effect_id=expired_row.effect_id;
  affected:=affected+1;
 END LOOP;
 RETURN affected;
END $$;
REVOKE ALL ON FUNCTION zabalack.expire_due_effects(integer) FROM PUBLIC;
UPDATE zabalack.installation SET schema_version=3 WHERE singleton;
