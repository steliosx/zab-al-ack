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
| [0.1.0b1+34ea703/](0.1.0b1+34ea703/) | `0.1.0b1`, tested source `34ea703` | adoption commits `3e58c63` and `1a6f0da`, unchanged through `2a4ce07` | [MANIFEST.json](0.1.0b1+34ea703/MANIFEST.json) |

`tools/public_audit.py` checks the bindings of the named current records
only. It scans every exported file, these included, for private material.
`tools/collect_ci_evidence.py` keeps this directory in a hosted run's
checkout, byte for byte, when it clears `evidence/` for that run's reports.
- Before anything is removed, it checks each version directory: exactly the
  regular files its `MANIFEST.json` lists, with their sizes and SHA-256s, and no
  links or special files. Otherwise it refuses.
- No producer artifact can write here, and history never counts as current-run
  evidence.
- A hosted candidate export therefore carries this history unchanged, together
  with only that run's current reports.
