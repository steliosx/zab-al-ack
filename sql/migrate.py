#!/usr/bin/env python3
"""Explicit checksummed migration runner. No trigger attachment; no implicit grants."""
import argparse
import hashlib
import os
from pathlib import Path
import psycopg

ROOT=Path(__file__).resolve().parent

def migrate(conn):
    with conn.transaction():
        if conn.execute('SELECT current_user').fetchone()[0] != 'zabalack_owner':
            raise RuntimeError('Run under explicitly selected zabalack_owner authority')
        conn.execute('SELECT pg_advisory_xact_lock(619325040011)')
        if conn.execute("SELECT pg_catalog.to_regnamespace('ackline') IS NOT NULL").fetchone()[0]:
            raise RuntimeError('legacy_ackline_schema_present: fresh database required; see docs/RENAME.md')
        conn.execute('CREATE TABLE IF NOT EXISTS zabalack.schema_migrations(version text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT clock_timestamp())')
        for path in sorted((ROOT/'migrations').glob('*.sql')):
            blob=path.read_bytes(); digest=hashlib.sha256(blob).hexdigest()
            row=conn.execute('SELECT sha256 FROM zabalack.schema_migrations WHERE version=%s',(path.name,)).fetchone()
            if row:
                if row[0]!=digest: raise RuntimeError(f'Migration drift: {path.name}; no automatic replacement')
                continue
            conn.execute(blob.decode())
            conn.execute('INSERT INTO zabalack.schema_migrations(version,sha256) VALUES(%s,%s)',(path.name,digest))
            print(f'APPLIED {path.name} {digest}')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dsn-env',default='ZABALACK_MIGRATION_DSN')
    parser.add_argument('--set-role',choices=['zabalack_owner'])
    args=parser.parse_args()
    with psycopg.connect(os.environ[args.dsn_env],autocommit=True) as connection:
        if args.set_role: connection.execute('SET ROLE zabalack_owner')
        migrate(connection)
