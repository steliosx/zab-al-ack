"""Scheduling regressions: background-slot contention must retain due work."""
from types import SimpleNamespace

import pytest

from zabalack.reconciler import Reconciler


class BusyBackgroundDB:
    def __init__(self, *, hosts=False):
        self.calls = []
        self.hosts = hosts
    async def call(self, operation, payload):
        self.calls.append(operation)
        if operation == 'claim_background':
            return None
        if operation == 'nightly_due':
            return {'actions': [{'instance': 'lab', 'action_id': '10000000-0000-4000-8000-000000000001', 'state': 'ok'}]}
        if operation == 'host_inventory_due':
            return [{'zabbix_instance': 'lab'}] if self.hosts else []
        return []


class NoRegistry:
    def resolve(self, instance):
        raise AssertionError('busy background slot must not resolve endpoint')


def setup(db):
    return Reconciler(SimpleNamespace(fast_budget=200, host_inventory_seconds=900,
        nightly_cycle_seconds=86400, recent_terminal_seconds=86400,
        retirement_cycle_seconds=900), db, NoRegistry(), worker_id='lab-worker')


async def test_T31_nightly_first_row_contention_does_not_defer_entire_sweep_one_day(monkeypatch):
    monkeypatch.setattr('zabalack.reconciler.time.monotonic', lambda: 100000)
    db = BusyBackgroundDB()
    reconciler = setup(db)
    reconciler._last_hosts = 100000
    await reconciler.scheduled_sweeps()
    await reconciler.scheduled_sweeps()
    assert db.calls.count('nightly_due') == 2, 'unprocessed first row must remain due after slot contention'


async def test_T33_host_inventory_contention_retains_due_remainder(monkeypatch):
    monkeypatch.setattr('zabalack.reconciler.time.monotonic', lambda: 100000)
    db = BusyBackgroundDB(hosts=True)
    reconciler = setup(db)
    reconciler._last_nightly = 100000
    await reconciler.scheduled_sweeps()
    await reconciler.scheduled_sweeps()
    assert db.calls.count('host_inventory_due') == 2, 'uninspected host inventory cannot wait another fifteen minutes'
