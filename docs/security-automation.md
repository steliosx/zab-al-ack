# Security analysis for contributors

Zab-al-ack has a commit-pinned CodeQL workflow for Python and JavaScript/TypeScript.
It covers the plugin, worker, deployment and release Python, the canonical Zabbix
webhook JavaScript, and the checked-in lab scripts. The `security-extended` suite
adds analysis to the existing advisory, secret, lint, type and invariant checks.
No source paths or individual security queries are suppressed.

## Enable on the selected repository

1. Review the repository's Code Scanning availability, source visibility and
   alert-access policy. Public repositories can use CodeQL; private repository
   licensing and permissions are owner inputs.
2. Configure advanced CodeQL setup for this workflow and disable any overlapping
   default setup. Set the repository variable `ZABALACK_CODEQL_ENABLED` to the
   literal value `true` after that review. No personal access token is required.
3. Run **CodeQL security analysis** with `workflow_dispatch`. Require successful
   Python and JavaScript/TypeScript analyses, and inspect the Code Scanning alert
   list for that exact commit. Record the run URL, commit, language categories,
   CLI version and unresolved findings in the release review.
4. After the first successful run, configure a repository ruleset to require
   CodeQL results and block unresolved high/critical security alerts. Review
   lower severity findings explicitly. Test the ruleset with a controlled
   pull request before relying on it as a merge gate.

The workflow runs for pull requests, pushes, manual dispatches and weekly at
05:17 UTC on Monday. A missing or false enablement variable produces a skipped
job. A skipped job or an analysis that has not finished is **NOT_RUN**, not a
clean security result. Fork pull requests use the `pull_request` event and its
GitHub-managed token restrictions; there is no `pull_request_target` fallback.
If repository policy prevents upload, fix the policy or keep the check blocked;
do not introduce a privileged fork workflow.

## Permissions and execution boundary

Only the analysis job requests `security-events: write`, to upload SARIF to the
repository's Code Scanning service. The workflow's default is `contents: read`.
Checkout does not retain credentials. There are no repository write, identity
token, release or package publishing permissions, and no custom secrets.

Both languages use `build-mode: none`. The pinned action no longer installs
Python dependencies, and this workflow does not run package installation,
project build scripts, tests or the plugin. It extracts checked-in source with
the pinned analysis engine. CodeQL database uploads and explicit debug mode are
disabled. SARIF may contain source locations or code; apply the repository's
source visibility policy before enabling uploads. Keep private inventories,
raw operational logs and runtime secrets out of every checkout.

Active CodeQL and dependency/secret jobs now select the dedicated repository
native Linux x64 self-hosted runner (ARM64 until 2026-09-22) with no paid-hosted fallback. This execution
policy needs Opus review alongside the CI migration. Existing CodeQL enablement,
permissions, query scope and ingestion requirements are unchanged; routing a
skipped job does not establish a clean scan. The pinned CLI 2.27.0/action support
native Linux x64 (and the former ARM64 host). CodeQL stays disabled by its gate. Validate host capacity and actual allocation before claiming
execution, and preserve private failed/interrupted attempts through the trusted
host hooks described in [self-hosted-runner.md](self-hosted-runner.md).

Action `v4.38.0` is pinned to its full commit. `tools: linked` selects CLI
`2.27.0` and its bundled queries from that commit's defaults; an explicit version
check rejects a different CLI. This does not independently hash-verify the
downloaded upstream CLI release asset. The inspected source hashes and exact
interfaces are in [upstream-codeql-manifest.json](upstream-codeql-manifest.json).
Review action, CLI and query changes together when updating this pin.

## Triage and evidence

Analyze each alert against a concrete trust boundary: actor/customer scope,
binding verification before endpoint access, SQL role separation, bounded source
writes, or release evidence integrity. Add a focused regression before fixing a
confirmed defect. A dismissal needs a documented reason and reviewer; do not
add broad path/query exclusions or automatic dismissals to make checks green.

CodeQL does not model the PostgreSQL `SECURITY DEFINER` ownership and grants as
deployment evidence, nor does its standard library understand every Alerta hook
or Zabbix JSON-RPC constraint. Native role tests, isolated end-to-end tests and
G1–G8 remain necessary. A successful workflow means analysis completed; it does
not by itself mean zero alerts or a passed merge-protection policy.

Historical workflow contract tests were local; the self-hosted routing change
does not establish CodeQL execution, SARIF ingestion, repository ruleset
enforcement or alert triage. Record each actual final-candidate run separately.
No scan result from another checkout is adopted as a result for this one.

References: [GitHub's advanced setup guidance](https://docs.github.com/en/code-security/code-scanning/creating-an-advanced-setup-for-code-scanning/customizing-your-advanced-setup-for-code-scanning),
[pinned action interfaces](https://github.com/github/codeql-action/tree/b96794f015dfd88f77b49b1c93e0fa7110f94c63).
