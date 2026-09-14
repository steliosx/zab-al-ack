# Zab-al-ack 0.1.0a16 GitHub handoff

The original a16 ZIP, complete Git-history bundle and review records are archived on this branch. All 37 files were verified against the local handoff. The original ZIP, bundle and 30 independent-review files are unchanged.

Continue development on [main](https://github.com/steliosx/zab-al-ack/tree/main). Site source is on [website](https://github.com/steliosx/zab-al-ack/tree/website).

Main commit [89a93b1](https://github.com/steliosx/zab-al-ack/commit/89a93b1f641a0f978b04862c747b8d4161df3f5a) passes all required hosted checks, including 42 native PostgreSQL/PgBouncer tests and packaging. See the [successful workflow](https://github.com/steliosx/zab-al-ack/actions/runs/34804234276) and [current review](REVIEW.md).

| File or directory | Contents |
| --- | --- |
| [REVIEW.md](REVIEW.md) | Current review, CI corrections, hosted results and upload verification. |
| [zabalack-0.1.0a16-complete.zip](zabalack-0.1.0a16-complete.zip) | Original uploaded ZIP, unchanged. |
| [source-history/zabalack-0.1.0a16.bundle](source-history/zabalack-0.1.0a16.bundle) | Complete original 16-commit candidate lineage. |
| [independent-review/RESULTS.json](independent-review/RESULTS.json) | Retained original review results and supporting records. |
| [github-import.json](github-import.json) | Import identities, historical and latest hosted results, archive status. |
| [ci-bootstrap-fix.patch](ci-bootstrap-fix.patch) | The separately committed initial CI bootstrap correction. |
| [SHA256SUMS](SHA256SUMS) | Checksums for every other file on this branch. |

After downloading this branch, verify its contents with `sha256sum -c SHA256SUMS` (macOS: `shasum -a 256 -c SHA256SUMS`).

For the original candidate history, clone the bundle into a separate directory:

```bash
git clone -b candidate/0.1.0a16 source-history/zabalack-0.1.0a16.bundle zabalack-a16-original
```

The original ZIP and bundle precede the CI maintenance fixes on main. Original candidate evidence remains bound to its original source and harness. The exact earlier 37-file proposal, including its historical status documents, is preserved in [archive commit 0a53667](https://github.com/steliosx/zab-al-ack/commit/0a536674505f01194c41f42fe8f549d60e84acf2).

This is a private development handoff. No new release tag, package-registry publication or deployment was performed; documented deployment gates remain open.
