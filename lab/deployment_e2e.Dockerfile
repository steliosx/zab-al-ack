# Fresh synthetic service lab only. Runtime networking is internal/no published ports.
FROM python:3.12.10-slim-bookworm
ENV TZ=UTC PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /opt/zabalack-lab
COPY requirements.lock .
RUN python -m pip install --no-deps -r requirements.lock
COPY . .
RUN python -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/wheels . \
 && python -m pip install --no-deps /tmp/wheels/zabalack-*.whl \
 && python -m pip check
CMD ["python", "-m", "pytest", "tests/deployment_e2e", "-q", "--tb=no", "--junitxml=/run/zabalack-e2e/junit.xml"]
