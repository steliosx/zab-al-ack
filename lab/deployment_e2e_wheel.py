"""Verify the supplied installed artifact before any service-test bootstrap."""
import json
import os
from pathlib import Path
import sys

from supplied_wheel import check_installed

ROOT = Path('/opt/zabalack-lab')
selection = json.loads((ROOT / 'qualified-wheel/selection.json').read_text())
identity = check_installed(ROOT / 'qualified-wheel' / selection['filename'], selection['sha256'], ROOT)
if '--verify-only' not in sys.argv:
    output = Path('/run/zabalack-e2e/installed-wheel.json')
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(identity, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.execv(sys.executable, [sys.executable, '-m', 'pytest', 'tests/deployment_e2e', '-q', '--tb=no',
                            '--junitxml=/run/zabalack-e2e/junit.xml'])
