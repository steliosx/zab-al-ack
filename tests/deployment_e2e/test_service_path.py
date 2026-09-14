"""Opt-in native deployment evidence; offline collection never starts services."""
import os

import pytest

pytestmark = pytest.mark.deployment


@pytest.fixture(scope="module")
def observed():
    if os.environ.get("ACK_DEPLOYMENT_E2E") != "GENERATED_ISOLATED_PROJECT":
        pytest.skip("NOT_RUN: use tools/run_deployment_e2e.py --isolated-lab")
    from lab.deployment_e2e_runtime import execute_service_path
    return execute_service_path()["checks"]


def test_installed_authenticated_path_and_exactly_one_source_effect(observed):
    assert all(observed[key] for key in ("authenticated_capture", "exactly_one_effect", "projection", "repeat_409"))


def test_real_customer_scope_and_api_key_cannot_sign(observed):
    assert observed["cross_customer_denied"] and observed["ingest_key_local_only"]


def test_native_runtime_login_privileges(observed):
    assert all(observed[key] for key in ("worker_denied_table_update", "probe_denied_table_read", "separate_native_logins"))


def test_real_media_macros_and_update_filter(observed):
    assert all(observed[key] for key in ("media_macro_ingestion", "neutral_update_filtered", "human_update_recorded", "recovery_macro_ingestion"))
