# Exact-artifact mode: no source build or fallback wheel is permitted.
FROM python:3.12.10-slim-bookworm
ENV TZ=UTC PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /opt/zabalack-lab
COPY requirements.lock .
RUN python -m pip install --no-deps -r requirements.lock
COPY . .
RUN python -m pip install --no-deps --no-index qualified-wheel/zabalack-*.whl \
 && python -m pip check \
 && python lab/deployment_e2e_wheel.py --verify-only
CMD ["python", "lab/deployment_e2e_wheel.py"]
