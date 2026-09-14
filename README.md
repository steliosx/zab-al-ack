# Zab-al-ack 0.1.0a16 proposed GitHub handoff

The development source is on [main](https://github.com/steliosx/zab-al-ack/tree/main), and the matching site source is on [website](https://github.com/steliosx/zab-al-ack/tree/website).

This is the prepared payload for `handoff/a16`. That branch has not been created: automatic approval review rejected adding the archive/history payload without more specific authorization. The source and website branches above are already pushed and verified.

The proposed payload contains:

| File or directory | Contents |
| --- | --- |
| [REVIEW.md](REVIEW.md) | Review, GitHub import verification, CI correction and the remaining native-CI failure. |
| [zabalack-0.1.0a16-complete.zip](zabalack-0.1.0a16-complete.zip) | The uploaded ZIP, unchanged. |
| [source-history/zabalack-0.1.0a16.bundle](source-history/zabalack-0.1.0a16.bundle) | Original 16-commit candidate lineage. |
| [independent-review/RESULTS.json](independent-review/RESULTS.json) | Retained local review results and supporting records. |
| [github-import.json](github-import.json) | Import identities and hosted-check results. |
| [ci-bootstrap-fix.patch](ci-bootstrap-fix.patch) | The separately committed CI correction. |
| [SHA256SUMS](SHA256SUMS) | Checksums for every other file in this proposed payload. |

After extracting this payload, verify with `sha256sum -c SHA256SUMS` (macOS: `shasum -a 256 -c SHA256SUMS`).

For the original candidate's Git history, clone the bundle into a separate directory:

```bash
git clone -b candidate/0.1.0a16 source-history/zabalack-0.1.0a16.bundle zabalack-a16-original
```

The original ZIP does not contain the subsequent CI bootstrap correction. Use main for continued development. The native PostgreSQL hosted job still fails and packaging is blocked; see the review for the exact run.
