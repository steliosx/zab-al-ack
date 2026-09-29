# Historical evidence

Each directory here preserves the evidence one earlier version carried at the
current `evidence/` paths, byte for byte, under a path that never changes.
- A record keeps the source, harness and wheel bindings of the run that
  produced it. It is not evidence for any later version.
- Never edit, rebind or replace these files. A later run is a new record at
  the current path, and the files here stay as they are.
- A preserved index is kept verbatim. Its relative links refer to its original
  location, `evidence/README.md`.

| Directory | Version | Origin | Manifest |
|---|---|---|---|
| [0.1.0a16/](0.1.0a16/) | `0.1.0a16` | import commit `d68fa91`, unchanged through `494768d` and `44f3ce4` | [MANIFEST.json](0.1.0a16/MANIFEST.json) |

`tools/public_audit.py` checks the bindings of the named current records
only. It scans every exported file, these included, for private material.
`tools/collect_ci_evidence.py` clears `evidence/`, except its index, in a
hosted run's checkout. So a hosted candidate export carries only that run's
evidence. The history stays in the repository and in local exports.
