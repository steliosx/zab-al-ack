PYTHON ?= python3

.PHONY: test quality mutation deployment-e2e quickstart wasm sql native native-local deployment-e2e-local property-nightly audit build
test:
	$(PYTHON) tools/run_reference_suite.py
quality:
	$(PYTHON) tools/run_quality.py
mutation:
	$(PYTHON) tools/run_mutation_checks.py --profile pr
deployment-e2e:
	$(PYTHON) tools/run_deployment_e2e.py --isolated-lab
sql:
	$(PYTHON) tools/run_sql_checks.py --isolated-lab
quickstart:
	$(PYTHON) tools/quickstart.py --isolated-lab
wasm:
	$(PYTHON) tools/run_reference_suite.py --wasm
native:
	$(PYTHON) tools/native_ci.py --isolated-lab
audit:
	$(PYTHON) tools/public_audit.py
build:
	$(PYTHON) tools/build_release.py
native-local:
	$(PYTHON) tools/native_local.py --isolated-lab
# Operator inputs: RUNNER_PYTHON=<venv python with the wheel installed> WHEEL=<dist/pypi/zabalack-*.whl> SERVICES=<services.json>
deployment-e2e-local:
	$(PYTHON) tools/run_deployment_e2e_local.py --isolated-lab --runner-python "$(RUNNER_PYTHON)" --wheel "$(WHEEL)" --services-file "$(SERVICES)"
property-nightly:
	$(PYTHON) tools/run_property_nightly.py --isolated-lab
