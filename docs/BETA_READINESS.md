# Beta readiness

The current candidate is 0.1.0a16. A beta requires the intended automation to
execute successfully on its exact commit in supported environments, plus the
publication-owner inputs. See `ALPHA_REPORT.md` for current executed results.
Current reference, mutation, property, native, real-Zabbix and deployment-path
records are digest-bound to this a16 tree (42 native, 6 real Zabbix 7.0.10, 4
deployment-path cases with 13/13 observations from the installed wheel).
Unchanged service-path code supports an inference about a14 and a15; a14's own
records remain NOT_RUN and a15's stand on a15's runs. The independent a12 and
a15 reviews' records — including the a15 review's local Gitleaks result — and
a14's qualitative agent evaluations are retained in the handoff as received and
are not rebound here. Earlier Ackline results remain historical as well.

Implemented in a14: task-specific agent instructions, three focused repository
skills, retained review attempts, separate mutation profiles and packaged
guidance provenance, with qualitative independent workflow evaluations. These
maintenance controls do not establish beta readiness or deployed permissions.

Implemented in a15: published evidence records carry a public file mode and the
export audit rejects an exported file that is not readable to group and other
(R20).

Implemented in a16: the independent a15 review is reproduced and adopted — ten
writer regressions across both public writers, both private writers and both
umasks, and corrections to release prose that overstated the R20 history, called
the profile mutation records required, attributed a15's service runs to a14 and
described the release verifier as stdlib-only (R21). Still required before beta:
G5's effective-role read-back and positive and negative permission tests on the
approved installed account (A4).

Implemented in a6: lint and gradual strict types, generated trust-boundary and
ledger tests, native worker crash/race tests, execution-owned service recording,
and current-run CI artifact collection with fail-closed packaging.

Implemented in a7: curated mutation gates, normal-process service-lab tooling,
opt-in CodeQL and inactive signed-attestation workflow plus offline verifiers.

Executed for the first time in a8, on source-built services (no Docker):

- Native PostgreSQL 18.3 / PgBouncer 1.24.1: all five phases including the
  worker crash/race tests, the generated ledger sequences and the 006→007
  settled-expiry correction (historical Ackline a8 evidence).
- Real Zabbix 7.0.10 API contract and end-to-end case through the a7 recorder
  (historical Ackline a8 evidence).
- The deployment-path lab: installed Alerta HTTPS process and worker CLI,
  ordinary signup/login, distinct password-authenticated SQL logins, media
  import, macro-expanded ingestion, neutral-update filtering, human update and
  recovery (historical Ackline a8 evidence). This run exposed and closed the
  reference webhook's cause-macro defect (A3) and three harness faults (R12).

Executed for the renamed namespace in a10, through the supplied
`tools/native_local.py` and `tools/run_deployment_e2e_local.py` (R14):

- Native PostgreSQL 18.3 / PgBouncer 1.24.1: 41 passed across the five phases,
  including the a9 legacy-schema guards executed on a real cluster.
- Real Zabbix 7.0.10: 5 passed through `tools/record_real_zabbix.py --run`.
- Deployment-path lab: 4 passed, 13/13 observations, from the installed a10
  wheel in a non-editable runner environment (closes the a8 review's F2).

Executed again in a13 on the reviewed tree (native 42, real Zabbix 6 including
the A4 excluded-action denial, deployment lab 4 from the installed a13 wheel).
Still required before beta: effective-role read-back and positive/negative
permission tests on the approved installed account (A4/G5).

Executed in a12 on the a11 (illustrated) tree through the same runners:
native 42 passed (walkthrough case added to the native `alerta` phase), real
Zabbix 5 passed, deployment lab 4 passed from the installed a12 wheel; browser
captures added, including a real Zabbix frontend screenshot of the lab event.

Still required before calling this beta ready:

- Execute hosted quality/reference/native/mutation and security workflows on
  the final commit; run the Docker Compose forms of the native and
  deployment-path labs (the a8/a10 reports used local processes; a10 proved the
  installed-wheel path on those local services, not the Compose image path).
- Resolve reference webhook transport amendment A2 with inspected installed
  controls and negative certificate/hostname/redirect tests before deployment.
- Observe media, macro and update-operation behaviour on a production-like
  Zabbix instance (the lab instance is fresh and synthetic).
- Review long-retained ledger query plans and production-like batch behavior.
- Assign the ten publication inputs in `config/publication.pending.json`, review
  licence obligations and arrange independent security review.
- Execute reviewed attestation issuance/verification to establish signed
  build/evidence provenance and a second-platform reproduction if those are
  part of the chosen beta release policy.

G1–G8 remain required for each deployment. Lab results cannot close them.
No push, tag publication, deployment or external notification is authorized by
these instructions. Follow `docs/RELEASING.md` for the reviewable release steps.
