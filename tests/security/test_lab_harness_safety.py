"""Ensure the executable batch harness cannot be aimed at real infrastructure."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('batch_harness', Path(__file__).parents[1] / 'performance' / 'run_batch.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


@pytest.mark.parametrize('url', [
    'http://192.0.2.1:8080', 'http://localhost:8080', 'http://127.0.0.1.example.invalid',
    'http://user:token@127.0.0.1:8080', 'file:///tmp/socket', 'http://127.0.0.1:8080?callback=x',
])
def test_batch_runner_refuses_nonloopback_or_ambiguous_target(url):
    with pytest.raises(ValueError):
        batch.validate_base_url(url)


def test_batch_runner_accepts_literal_loopback():
    assert batch.validate_base_url('http://127.0.0.1:8080/') == 'http://127.0.0.1:8080'
    assert batch.validate_base_url('http://[::1]:8080') == 'http://[::1]:8080'


def test_batch_runner_requires_exact_synthetic_population():
    with pytest.raises(ValueError):
        batch.validate_manifest({'environment': 'production', 'alerts': []}, 50)
    with pytest.raises(ValueError):
        batch.validate_manifest({'environment': 'isolated_synthetic_lab', 'alerts': []}, 50)
