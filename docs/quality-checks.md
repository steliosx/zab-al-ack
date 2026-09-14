# Python quality checks

Run from the repository root using Python 3.12. Install application and test
dependencies first, then the separate hash-checked quality tools:

```sh
python -m pip install -r requirements.lock
python -m pip install --require-hashes --only-binary=:all: -r requirements-quality.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m pip check
python -m ruff check src
python -m mypy --config-file mypy.ini
```

The quality lock contains development tools only and is not an application
dependency upgrade. Do not combine the two lock-file install commands: pip's
hash-checking mode would also require hashes on every application requirement.
An offline installation can add `--no-index --find-links /path/to/wheelhouse`
to each install command when its complete pinned wheel set is available.

## Enforced scope

`ruff.toml` requires Ruff 0.16.7 and checks all maintained Python code in `src`
for syntax/name errors, unused imports/variables, and Bugbear's likely defects
(`E4`, `E7`, `E9`, `F`, `B`). The command is a required failure-producing check.
It does not run a formatter or rewrite pinned upstream references or SQL.

`mypy.ini` uses strict mode for `config`, `crypto`, `policy`, and `rpc`, follows
imports normally, and contains no module-wide error exclusions or missing-import
suppression. Strict mode requires typed functions, checks their bodies, rejects
implicit optionals and unparameterized generics, and reports unsafe `Any`
returns and unused type ignores. It is a gradual boundary: the remaining worker,
database, Flask/Alerta, CLI, and scheduling modules are not yet strict targets.
Add modules to the explicit file list as their interfaces become typed.
Both tools keep caches under `build/cache/`.

`SourceSnapshot`, `ActorIdentity`, and `Intent` describe validated identity and
binding shapes. The cohort-selection protocol exposes only the approved cohort
collection. Raw JSON values enter as `object`; dynamic configuration and raw
database/upstream dictionaries retain explicit `Any` where runtime validation
remains the authority. The few casts sit immediately after the existing shape
validation, rather than asserting trust at an input boundary. An `Intent` type
alone proves neither its MAC nor actor authority: verification and authorization
still run in the existing order before endpoint or credential resolution.

No JSON keys, canonical encoding, marker format, timestamp serialization,
database migrations, role grants, or outbound RPC parameters change here.
The shape validator continues accepting timestamp objects, while canonical JSON
continues rejecting them; the constructor emits the existing UTC strings.

## Tool provenance and platform

The lock was inspected on 2026-09-11 against wheel `METADATA` and the official
PyPI release JSON. Each locked SHA256 was compared with both the downloaded
artifact and PyPI's digest for that filename. This lock covers CPython 3.12 on
Linux x86_64 with compatible manylinux wheels; another platform requires an
explicitly inspected wheel/hash update. There are no source builds or optional
tool extras in this lock.

| Direct tool | Pin | Package Python requirement | Official release metadata |
|---|---|---|---|
| Ruff | 0.16.7 | >=3.7 | [PyPI](https://pypi.org/pypi/ruff/0.16.7/json) |
| mypy | 2.3.1 | >=3.10 | [PyPI](https://pypi.org/pypi/mypy/2.3.1/json) |
| Hypothesis | 6.168.0 | >=3.10 | [PyPI](https://pypi.org/pypi/hypothesis/6.168.0/json) |

The full transitive closure for this Python/platform is pinned in
`requirements-quality.lock`: `ast-serialize`, `librt`, `mypy-extensions`,
`pathspec`, `sortedcontainers`, and `typing-extensions`. Each entry records its
official metadata URL, inspected filename, version, and SHA256. The
`typing-extensions` pin agrees with the application/test lock. Hypothesis is
available to the bounded property tests without adding a production dependency.

To update, edit the direct pins in `requirements-quality.in`, use a clean Python
3.12 environment to download binary wheels with `python -m pip download
--only-binary=:all: -r requirements-quality.in --dest build/quality-wheels`,
inspect all resolved `Requires-Python` and active `Requires-Dist` metadata, and
compare downloaded hashes with the exact official PyPI release metadata.
Record every active transitive dependency and the selected wheel hashes in the
lock. Review any changed rule/type behavior, update Ruff's required version,
then rerun the hash-checked install, `pip check`, both gates, and affected tests.
Do not silently relax rules or ignore errors to accommodate a tool upgrade.

## Behavioral verification

The existing focused suite exercises configuration/crypto policy, capture health,
worker verification order, plugin signing/privacy, exact RPC scope, response
validation, and read-only reconciliation scheduling:

```sh
python -m pytest -q tests/unit/test_crypto_policy.py tests/unit/test_health.py tests/unit/test_worker.py tests/unit/test_plugins_dispatcher.py tests/rpc/test_rpc.py tests/security/test_rpc_boundaries.py tests/security/test_reconciler_scheduling.py
```

Initial execution: **126 passed, 0 failed, 0 skipped**; 50 warnings originate
from the pinned Alerta dependency's deprecated `datetime.utcnow()` calls.
Ruff passed for `src`; strict mypy passed for four source modules. These static
and synthetic tests do not establish native PostgreSQL concurrency,
authentication, cancellation, PgBouncer, HA, or deployment gates G1–G8.
