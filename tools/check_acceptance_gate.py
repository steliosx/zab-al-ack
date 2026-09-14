#!/usr/bin/env python3
"""Fail a deployment/CI gate unless all native T and G evidence is truly passed.

Unit/mocked/WASM reference results never satisfy this gate. This does not enable
external writes, alter the database, or generate evidence for missing tests.
"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path

# T40/T41 are the amendment A5 additions and are required for deployment
# acceptance like every other catalogue item; A5 stays open until T41 passes.
REQUIRED = {f'T{i}' for i in range(1,42)} - {'T11'} | {'T11a','T11b'}

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True,
        help='Reviewed JSON: acceptance map + G1..G8 map, each with status, evidence_ref, environment')
    args=parser.parse_args()
    data=json.loads(args.evidence.read_text())
    missing=[]
    for group,keys in [('acceptance',REQUIRED),('deployment_gates',{f'G{i}' for i in range(1,9)})]:
        entries=data.get(group,{})
        for key in sorted(keys):
            entry=entries.get(key,{})
            if (entry.get('status')!='PASS' or not entry.get('evidence_ref')
                    or entry.get('environment') not in {'native_staging','deployment'}):
                missing.append(key)
    print(json.dumps({'accepted':not missing,'open_requirements':missing,'external_writes_enabled':False},indent=2))
    return 0 if not missing else 2

if __name__=='__main__':raise SystemExit(main())
