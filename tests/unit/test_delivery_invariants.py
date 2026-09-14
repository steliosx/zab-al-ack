"""Independent missing guard cases, with database and remote effects mocked.

These check worker decisions, not SQL lease authority or native process fencing.
"""
from copy import deepcopy

import pytest

from zabalack.policy import PolicyError, authorize_intent
from test_worker import FakeDB, Client, action, signed_capture, worker, COHORT, event


@pytest.mark.asyncio
async def test_unknown_delivery_does_not_resolve_target_or_start_an_attempt():
    row, db, client = action(), FakeDB(), Client()
    row['uncertain'] = True
    instance = worker(db, client)
    await instance.deliver(row)
    instance.registry.resolve.assert_not_called()
    assert not client.reads and not client.writes
    assert not any(op == 'start_attempt' for op, _ in db.calls)
    assert db.outcomes()[-1]['uncertain'] is True


@pytest.mark.asyncio
async def test_missing_durable_attempt_never_sends_a_request():
    class FencedDB(FakeDB):
        async def call(self, operation, payload=None):
            result = await super().call(operation, payload)
            # A false/empty attempt result must be handled before accessing its
            # ID. A mapping with a None ID permits an unsafe mutation to proceed
            # far enough for the observable no-write assertion to detect it.
            return {'attempt_id': None} if operation == 'start_attempt' else result

    db, client = FencedDB(), Client()
    await worker(db, client).deliver(action())
    assert len(client.reads) == 1
    assert not client.writes
    assert db.outcomes()[-1]['reason'] == 'lease_fenced'


def test_authorised_actor_still_needs_a_one_to_one_cohort():
    cohort = deepcopy(COHORT)
    cohort['one_to_one'] = False
    with pytest.raises(PolicyError, match='cohort_not_authorised'):
        authorize_intent(signed_capture()['intent'], cohort)


@pytest.mark.asyncio
@pytest.mark.parametrize('field,replacement', [('eventid', '41002'), ('objectid', '21002')])
async def test_valid_shaped_response_for_another_event_or_trigger_cannot_be_written(field, replacement):
    observed = event()
    observed[field] = replacement
    db, client = FakeDB(), Client([observed])
    await worker(db, client).deliver(action())
    assert client.reads[0][0] == '41001'
    assert not client.writes
    assert not any(operation == 'start_attempt' for operation, _ in db.calls)
    assert db.outcomes()[-1]['reason'] == 'event_trigger_mismatch'
