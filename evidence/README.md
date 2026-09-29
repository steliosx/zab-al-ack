# Zab-al-ack evidence

This index covers the current candidate, `0.1.0b1`, an unpublished local beta
candidate (see [beta readiness](../docs/BETA_READINESS.md)). Every record
identifies the source and harness bytes it executed on, and describes only
that run.

## 0.1.0b1: PENDING

No record in this directory is evidence for `0.1.0b1` yet. Until this
candidate's own runs are adopted, the files at the current paths are still
the `0.1.0a16` records, byte for byte. They are bound to a16's source and
harness. So `tools/public_audit.py` reports them as not bound to this tree,
and `tools/build_release.py` refuses to export.

- Local release checks on this candidate: NOT_RUN. These are quickstart,
  quality, mutation, SQL, reference and property
  ([releasing](../docs/RELEASING.md)).
- Deployment path with this candidate's exact wheel: NOT_RUN. The lab run that
  passed on `494768d` is evidence for `494768d` only, and it is never rebound
  to a later tree.
- Native PostgreSQL/PgBouncer and real Zabbix: NOT_RUN on this candidate.

This candidate's records will replace the current paths in one change,
together with this index, the
[compatibility manifest](../docs/compatibility_manifest.json) and the version
narrative. Preparation, local and hosted runs stay distinct.

## Historical evidence

[history/](history/README.md) holds byte-exact copies of earlier versions'
records, under paths that never change.
- `history/0.1.0a16/` preserves every file this directory carried for a16,
  including its original index. Its `MANIFEST.json` lists each file's SHA-256,
  size, Git blob and original path.
- The a16 records keep their original bindings and counts:
  - native: 42 passed;
  - real Zabbix: 6 passed, including the A4 excluded-action denial;
  - deployment path: 4 passed, with 13/13 observations from the installed
    a16 wheel;
  - reference: 865 passed and 23 skipped.

The public export and acceptance tools still reject stale, malformed or
contradictory evidence. G1–G8 remain pending, and A2 is unresolved. Docker
Compose, hosted workflows, external security review and signed attestation
remain unexecuted or pending.
