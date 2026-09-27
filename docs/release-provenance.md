# Release integrity and optional signed provenance

Zab-al-ack's local build produces an **unsigned** `BUILD_PROVENANCE.json`. It
records source/harness digests and the byte comparison of two wheel, normalized
sdist and source ZIP builds. Matching hashes establish consistency with those
inputs. They do not identify a publisher or authenticate the manifest.

Verify a freshly built candidate against its source checkout with:

```bash
python tools/build_release.py
python tools/verify_release.py
```

The verifier reads archives without extracting or executing them. It rejects
unsafe paths, links, duplicate members, excessive expanded sizes, changed bytes,
missing subjects and unreviewed files. It compares the public source archive to
the checkout, the sdist to that archive and wheel code to `src/zabalack`. Wheel
metadata is checked against `pyproject.toml`: package identity, Python and
dependency constraints, extras, plugin and CLI entry points, install layout,
licences and every RECORD hash/size. The metadata policy intentionally follows
the pinned reference setuptools build; changes to that packaging interface
require a reviewed policy update.

The successful result is `scope: unsigned_release_consistency` with
`signed_attestation: false`. Neither this result nor a fresh checksum list makes
a candidate signed, secure, production approved or independently reproduced.

When a deployment-lab report is present in the source archive, its reported
counts, required observations, cleanup, runtime identities, versions and source
binding must also pass the shared deployment-evidence validator. Hashing a
contradictory report does not make it valid. Local-process reports keep their
distinct scope: they establish the installed-wheel service path on the named
local services (R14) and do not prove Docker execution or the Compose-only
exact-artifact `supplied_wheel` receipt.

## Deliberate hosted issuance

Routine CI now targets the dedicated native Linux x64 self-hosted runner (ARM64
until 2026-09-22), with no
hosted fallback; see [ci-evidence.md](ci-evidence.md). This changes the execution
policy for unsigned candidate validation and requires Opus review. It does not
change the optional signed-attestation consumer policy below. The inactive
attestation/publication templates still contain hosted jobs and remain disabled.
Before enabling either, the owner must separately review runner routing and the
signing trust boundary. The existing verifier continues to reject self-hosted
signers with `--deny-self-hosted-runners`; do not remove that check merely to
make a self-hosted attestation pass. Unsigned self-hosted Actions packaging can
be validated while this optional signing-policy decision remains pending.

`release/attest.yml.example` is an **inactive template**, outside live workflows.
It creates no attestation until an owner deliberately installs it as
`.github/workflows/attest.yml`, configures the repository and enables it. No
package upload, Git tag creation or release publication is included.

The proposed sequence uses the existing CI as a reusable workflow. It repeats
the required quality, reference, mutation and native gates for the selected
commit, then builds the candidate. The signing job downloads only the candidate
from that same Actions run and attempt. It revalidates the collection context,
required report content, hashes, source/harness digests and correspondence between
archived and downloaded evidence before copying four validated subjects into a
fresh fixed directory. There is no input for an arbitrary artifact path, foreign
repository or previous run. The action hashes the staged files itself.

Only the signing job requests `id-token: write` and `attestations: write`; all
other jobs keep `contents: read`. The template requires a manual dispatch,
`ZABALACK_ATTESTATIONS_ENABLED=true`, a reviewed commit equal to the selected tag
commit, and a tag matching the candidate version in `pyproject.toml`. Configure the
`release-attestation` environment with reviewers and reviewed-tag restrictions
before enabling it; the mere environment name does not install those protections.

Enabling this template authorizes an external write: the action stores the
attestation with GitHub, and public repositories use Sigstore's public signing
infrastructure and transparency log. Keep this separately authorized. The
template sets both registry push and artifact storage-record creation to false.
GitHub repository plan/visibility support, environment protections and actual
issuance still need owner validation. Building this candidate did not perform
any of those hosted actions.

## Consumer verification

Obtain the expected repository, reviewed tag and full commit from the release
owner through an independently trusted channel. A checksum file delivered beside
an artifact is insufficient to establish that identity.

Install the reviewed GitHub CLI **2.83.2** from its official release or build its
inspected commit according to the upstream instructions; review its distribution
integrity independently. The helper checks the version and runs that verifier
itself. It never accepts a caller-supplied JSON report as evidence.

```bash
export ZABALACK_RELEASE_REPOSITORY='<approved-owner>/<approved-repository>'
export ZABALACK_RELEASE_COMMIT='<reviewed-40-hex-commit>'
python tools/verify_attestation.py dist/pypi/zabalack-0.1.0b1-py3-none-any.whl \
  --repo "$ZABALACK_RELEASE_REPOSITORY" \
  --source-ref refs/tags/v0.1.0b1 \
  --source-digest "$ZABALACK_RELEASE_COMMIT"
```

This is a read-only verification lookup, not issuance. For a downloaded bundle,
add `--bundle /path/to/attestation.json`. To use a separately obtained trusted
root for offline verification, also add `--trusted-root /path/to/trusted_root.jsonl`.
A local bundle alone may still require trust-root retrieval. Private repository
lookups may require the engineer's existing local GitHub authentication; do not
paste credentials into project files or chat.

The command enforces the exact certificate identity
`https://github.com/OWNER/REPO/.github/workflows/attest.yml@refs/tags/VERSION`,
GitHub's OIDC issuer, the exact source ref and source/signer commit, the SLSA v1
predicate and a GitHub-hosted runner. It checks the signed subject digest and
rejects changed local inputs. The inspected CLI's `--signer-workflow` option
builds a prefix regular expression; this wrapper uses `--cert-identity` for an
exact match instead. Policy depends on certificate claims verified by the CLI,
not on user-editable fields in `statement.predicate`.

Successful execution returns `VERIFIED_GITHUB_ATTESTATION`. Missing tooling or
inputs, an incompatible verifier, malformed output or a failed signature/policy
check cannot produce that result. Verification establishes provenance under the
chosen workflow trust policy. It does not establish absence of vulnerabilities,
any SLSA assurance level, deployed authorization or G1–G8 acceptance.

## Evidence and references

The new tests exercise synthetic consistent and tampered artifacts, exact
certificate-policy command construction, verifier failures and malformed output.
They are offline unit tests. **Hosted issuance and cryptographic verification of
an Zab-al-ack attestation are NOT_RUN**, because no Zab-al-ack attestation has been
issued in this workspace. Keep `BUILD_PROVENANCE.json` unsigned after signing;
the separate issued bundle is the cryptographic evidence.

Actual action and CLI sources inspected at immutable commits are recorded with
file hashes in `upstream-attestation-manifest.json`:

- [actions/attest v4.2.2](https://github.com/actions/attest/tree/1e69f48acb82d1966a394da916b4c1698aa569d6)
- [GitHub CLI v2.83.2 verification policy](https://github.com/cli/cli/blob/560ea94fc7d63ab1f56ca47713599be5e8966fd3/pkg/cmd/attestation/verify/policy.go)
- [GitHub CLI certificate extension enforcement](https://github.com/cli/cli/blob/560ea94fc7d63ab1f56ca47713599be5e8966fd3/pkg/cmd/attestation/verification/extensions.go)

The installed action uses Node 24. With no custom predicate/SBOM input it
generates build provenance. Its `subject-path` input reads and hashes explicit
files, and `bundle-path` identifies the issued bundle. These interfaces were
inspected from official source; hosted execution is a separate open check.
