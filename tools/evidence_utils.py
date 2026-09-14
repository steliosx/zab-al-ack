"""Sanitise exported evidence without replacing failures/skips with passes."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import re
from pathlib import Path
import xml.etree.ElementTree as ET

# Every executed-run record is bound to the tree it ran against: `source` covers
# the shipped implementation, `harness` the tests, runners, labs and pinned
# dependency locks. Documentation, evidence and built artifacts are outside both
# scopes, so regenerating a report or editing prose never invalidates a run while
# any change to what was executed does. tools/update_acceptance.py refuses to
# map a record whose digests differ from the tree it is invoked on.
SOURCE_SCOPE = ('src', 'sql')
HARNESS_SCOPE = ('tests', 'tools', 'lab', 'zabbix', 'collectors', 'pyproject.toml', 'setup.cfg',
                 'requirements.lock', 'requirements-runtime.lock', 'requirements-scanner.lock',
                 'requirements-quality.lock', 'requirements-quality.in', 'ruff.toml', 'mypy.ini',
                 '.github', 'release', 'config', 'deploy', 'Makefile', 'MANIFEST.in')
DIGEST_EXCLUDED = {'node_modules', '__pycache__', '.pytest_cache', '.lab', '.git'}


def scoped_digest(root: Path, scope: tuple[str, ...]) -> str:
    """SHA-256 over the sorted relative paths and byte hashes of every file in scope."""
    digest = hashlib.sha256()
    for entry in scope:
        candidate = root / entry
        files = [candidate] if candidate.is_file() else sorted(candidate.rglob('*')) if candidate.is_dir() else []
        for path in files:
            relative = path.relative_to(root)
            if not path.is_file() or path.is_symlink() or path.suffix in {'.pyc', '.pyo'}:
                continue
            # setuptools writes this generated metadata inside src/ during an
            # editable install or build. It is not executable source, and the
            # public source selector excludes it too. Including it would make
            # a successful build invalidate the evidence for its own inputs.
            if any(part in DIGEST_EXCLUDED or part.endswith('.egg-info') for part in relative.parts):
                continue
            digest.update(relative.as_posix().encode() + b'\0')
            digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode() + b'\n')
    return digest.hexdigest()


def code_digests(root: Path) -> dict[str, str]:
    return {'source': scoped_digest(root, SOURCE_SCOPE), 'harness': scoped_digest(root, HARNESS_SCOPE)}


def binding_error(record: object, root: Path, name: str) -> str | None:
    """Explain why a run record is not bound to the tree at root, or None when it is."""
    if not isinstance(record, dict):
        return f'{name} run record is malformed; cannot rebuild acceptance mapping'
    recorded = record.get('code_digests')
    if not isinstance(recorded, dict):
        return f'{name} run record carries no code digests; rerun it on this tree before mapping'
    expected = code_digests(root)
    for scope in ('source', 'harness'):
        value = recorded.get(scope)
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
            return f'{name} run record {scope} digest is malformed; rerun it on this tree before mapping'
        if value != expected[scope]:
            return f'{name} run record was produced on a different {scope} tree; rerun it on this tree before mapping'
    return None


def utc_timestamp(value: str) -> str:
    """Render a JUnit timestamp in UTC so evidence carries no local-zone offset."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        # pytest writes naive local time when no zone is attached; it cannot be
        # converted reliably, so it is only truncated to the second.
        return parsed.replace(microsecond=0).isoformat()
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def public_text(value: str, root: Path) -> str:
    value = value.replace(str(root.parent), '<workspace>')
    value = re.sub(r'/workspace/[^\s\"\'<>:]+', '<workspace-path>', value)
    value = re.sub(r'/root/[^\s\"\'<>:]+', '<home-path>', value)
    value = re.sub(r'\[(?:zabalack|ackline):v1\s+action=[^\]]+\]', '[redacted-signing-proof]', value, flags=re.I)
    value = re.sub(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', '[redacted-key-header]', value)
    value = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b', '[redacted-token]', value)
    value = re.sub(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b', '[redacted-access-key]', value)
    return value


def redact_junit(raw: Path, output: Path, root: Path) -> None:
    tree = ET.parse(raw)
    for element in tree.iter():
        element.attrib.pop('hostname', None)
        if 'timestamp' in element.attrib:
            element.set('timestamp', utc_timestamp(element.attrib['timestamp']))
        for key, value in list(element.attrib.items()):
            element.set(key, public_text(value, root))
        if element.text:
            element.text = public_text(element.text, root)
        if element.tail:
            element.tail = public_text(element.tail, root)
    output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output, encoding='utf-8', xml_declaration=True)


def cases(path: Path) -> list[dict]:
    rows = []
    for case in ET.parse(path).getroot().iter('testcase'):
        status = 'passed'
        if case.find('failure') is not None or case.find('error') is not None:
            status = 'failed'
        elif case.find('skipped') is not None:
            status = 'skipped'
        rows.append({'nodeid': case.get('classname', '') + '::' + case.get('name', ''),
                     'status': status})
    return rows
