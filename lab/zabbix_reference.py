"""The exact Zabbix 7.0 patch every lab producer and validator targets (R27).

One file, `lab/zabbix_reference.json`, carries the selection, the source-level
facts verified for it and the package verification gate. Producers compare the
observed `apiinfo.version` with it; validators require it in a passing record.
Historical records keep the version they observed.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

PATH = Path(__file__).resolve().parent / 'zabbix_reference.json'


def load() -> dict:
    record = json.loads(PATH.read_text())
    selected = record['selected']
    if (not re.fullmatch(r'7\.0\.[0-9]+', selected['version'])
            or not re.fullmatch(r'[0-9a-f]{40}', selected['tag_commit']) or selected['series'] != '7.0'):
        raise ValueError('lab/zabbix_reference.json selection is malformed')
    return record


VERSION: str = load()['selected']['version']
TAG_COMMIT: str = load()['selected']['tag_commit']
