# ack-core 3b pre-read — source identity and inventory (item 6)

Author: Opus (Zabalack session), 13 October 2026. All files in this folder are inert review data. Nothing is
to be built or run. No private configs, credentials, keys, session logs or terminal snapshots are attached.

## Classification of claims

| Class | Where |
|---|---|
| **Existing reviewed behaviour** (merged on `main`, reviewed by Codex) | `01-handoff-v3.md` §1 inventory; R4 §1; R2 §1 source facts; the designs named below |
| **Proposed design** (not accepted, not implemented) | R2 §2-§5, R3 §2-§4, R4 §2-§3, R5 §1-§3, `06-joint-witness-matrix.md` |
| **Unverified runtime** | everything about runtime behaviour; no claim here was executed. The `UPDATE OF` firing rule (R2 §1) is a PostgreSQL documentation fact plus a pinned-source reading, pending a native witness |

## Zabalack source identity

| Item | Value |
|---|---|
| Repository | `steliosx/zab-al-ack` (public) |
| Base commit (`main`) | `d06a363259633918f3df2eedf58c99ecc5468414` |
| Tree | `ec48820feac40ecd5ca7bc7bf15f43bb5b096daa` |
| Parents | `92aa6ba4cad4dcae92c5b6a5f924dbae862edc9c`, `0b4e8d5808f82d532245d7fb968c876ad06700ab` |
| `git archive --format=tar d06a3632` | sha256 `df632c177979698a44ae18201203eccbd7c1fd997c1d00ea03b9c171990835ec`, 6,758,400 bytes, 374 tracked files |
| Pre-read branch | `docs/ack-core-3b-preread`, adds only files under `docs/ack-core-3b-preread/` (commit, tree and per-file hashes in the delivery message) |

Cited Zabalack files at `d06a3632` (Git blob, sha256):

| Path | Blob | sha256 |
|---|---|---|
| `src/zabalack/plugins.py` | `f5ebf90f2bfaf9cca76a1b701599cf871ccbbb37` | `2a128e7ad4cd835a75e6633b3deca4a12690fb87fe551436423f0fdfd68efcc8` |
| `src/zabalack/normalizer.py` | `4941dc15da486e9601b619fcfd4c737569b43ab0` | `b7667f1ddbd70a08ad773bcf209e8ff0a380b2fb2b0cf8bfad1aa753aa22356c` |
| `src/zabalack/query_guard.py` | `a57bb448b6b1a9d88481d459b4635fb554179cb4` | `d1e2a195dd18aba6f4d738209a15fc5955ab98ef897636dd4da6ebd4d30a9299` |
| `src/zabalack/rpc.py` | `9bf930fd4612fceea5165a365aa0d55efae279d3` | `3944e2d54dd5a0ce31a1ba27cfc5b0e2dd8af584fdbdc6af5719cb7032c65bab` |
| `sql/migrations/008_generation_fence.sql` | `0f157827ae94d670a6fff0f62c6c8fd4f4d1c86d` | `8dc04f4b5f61100a709242ac0d0546fd56464df949a662c6c409faa7ab118873` |
| `sql/install/00_roles.sql` | `ddbae21bf6ec3f9823620a4b44bad55986324e81` | `0ad010ce151ec74457ad6e3e206c3cf2c21188bb974ced2506ae957344d62856` |
| `sql/install/10_trigger.sql` | `71ffa283b98c1c253c2236d436996699827595df` | `36b759636c34e336d6c5564aa4f5a8fe3329b3a958b9a5c27e58a10f8a29563b` |
| `sql/install/12_application_grant.sql` | `91c12e46f5b2957094a9b3c0a2fd1269e042abad` | `857ae750952e39fd8ae865a95c20bb8d6ed3c83425ea3837b389b18d2402f014` |
| `sql/install/13_reseal.sql` | `ea816c3073cc2d564a987402bec71c995bd94847` | `34f89e3192197359a435bcad1239f694007b8d326fc8611bb0feb0d19d8cfaa5` |
| `docs/DESIGN_0.2.0_ACTION_SYNC.md` | `e8409a24cae286ba90c89b7ce95003f4b972bddf` | `54d1cf6d964fb8ab49aec6373368c9aadbaf11fc7c09a37a73f7ee9054122169` |
| `docs/DESIGN_0.2.0_F1_SOURCE_BINDING.md` | `d3c9b5ffbe0c7ad8659861c815d16ee38afc42e4` | `cba7d4b13967309c36a42aa5c85f1a11453eba55e5598356dd4a490f0f1642d9` |

## Alerta identity (consumed callback, session and commit paths)

| Item | Value |
|---|---|
| Package | `alerta-server==9.1.0` (pinned in `requirements.lock`) |
| PyPI wheel | `alerta_server-9.1.0-py2.py3-none-any.whl`, sha256 `50e3d49655e14a2361341871901b9a23a8a99658c1c93316a636f8585ab3e2df`, 169,103 bytes |
| PyPI sdist | `alerta_server-9.1.0.tar.gz`, sha256 `5e08264b7d526977c5475678330cad447652ac1284c90104cf33c4145771eb19`, 212,763 bytes |
| Upstream commit | `f4312727783dad64198b62ed778bbca98a5144a3` (as resolved by Codex for v9.1.0) |

Cited files (sha256, bytes). **Provenance classes:** Codex independently retrieved these seven files at
upstream commit `f4312727` and matched the sha256 and byte counts below (review
20261013T163920Z-codex-omarchy-750d397d). The statements that they are byte-identical to the PyPI wheel and
to the installed reference venv, the wheel and sdist hashes above, and the `git archive` inventory are
**peer evidence** from the Zabalack session. They are not reviewer-established artifact provenance.

| Path | sha256 | Bytes |
|---|---|---|
| `alerta/utils/api.py` | `5853831e34406205a1a5cc1e2080a368ff4afe4aa52277a06eedbd5d2fa362b2` | 8124 |
| `alerta/views/alerts.py` | `56ba8984adb368207609d363a753fd1149a7e1552c5652f17af91e9571a16f5e` | 23773 |
| `alerta/models/alert.py` | `50fd404dbc435ee40fea8dd95fa5e874b8a904c2fa31507c87bccfec7e665f3b` | 26062 |
| `alerta/database/base.py` | `f62b344cc5b1955d277756b8464d828c9d4f2ac6433d065165cdc218e1c5c17d` | 14634 |
| `alerta/database/backends/postgres/base.py` | `a9d24ba7d6446356847c7d0a01dacc8de21f0d835893ebc5f29704c407392a84` | 62411 |
| `alerta/utils/hooks.py` | `7090b15b60b8ca6999b02c5cfecefd381e0a403723f143740cf7051696e9b369` | 1515 |
| `alerta/management/views.py` | `d1d3d0b88ad820ec9e589486fb82f250bbbb9dac7834635fea46ea15845f2d4b` | 9075 |

## Dependency pins at `d06a3632` (`requirements.lock`)

`alerta-server==9.1.0`, `Flask==3.1.3`, `Werkzeug==3.1.9`, `blinker==1.9.0`, `psycopg2-binary==2.9.10`.
PostgreSQL reference pin 18.6 (R25). ack-core has no version yet (open item R5 §4.1).

## Handoff history (item 1)

| Version | Size | sha256 | How it reached Voalack/Astra |
|---|---|---|---|
| v1 (pre-read, 12 Oct) | 5,697 B | `f592d0c9c73ee8a817a64bcb389a13d358d73d2f58a51fe147cb0c540017a7c0` | summarised inline via SendMessage (not the bytes) |
| v2 (+ §5 positions) | 9,110 B | `02ae6e312c3703a25d479a899806b36deff236b1c2a5796b4a6f34e739513c8d` | positions summarised inline via SendMessage |
| **v3 (this folder, `01-handoff-v3.md`)** | 11,466 B | `b150bacac4e20a2519b183212215b31dcd3676ff24530d642576d0292d370915` | first delivery of exact bytes |

Delta v1 to v2: §5 added (Astra's answers, Zabalack's provisional positions).
Delta v2 to v3: §2 rewritten to match §5 (per-alert lock ordering, no independent staging durability,
managed-conflict refusal, `model_unack` diagnostic-only); `CaptureLogRedactor` added to the §1 inventory;
question 1 of §4 marked answered; §6 added (Codex R2-R5 as open contracts).

## R2-R5 drafts

| File | Contract | Author | Reviewer disposition |
|---|---|---|---|
| `02-R2-ingestion-epoch.md` | R2 | Opus | Codex r1: CHANGES_REQUIRED (P1, P3, P6); r2: CHANGES_REQUIRED (Q1, Q2); r3 answers them, review pending |
| `03-R3-model-unack.md` | R3 | Opus | Codex r1: CHANGES_REQUIRED (P2, P3, closure); r2: CHANGES_REQUIRED (Q1, Q2, Q4); r3 answers them, review pending |
| `04-R4-transaction-commit-map.md` | R4 | Opus | Codex r1: CHANGES_REQUIRED (P4, P7); r2: CHANGES_REQUIRED (Q1, Q3); r3 answers them, review pending |
| `05-R5-shared-install-resolver.md` | R5 | Opus | Codex r1: CHANGES_REQUIRED (P1, P5); r2: CHANGES_REQUIRED (Q1, Q2, Q4); r3 answers them, review pending |
| `06-joint-witness-matrix.md` | item 7 | Opus | Codex r1 and r2: rows requested; r3 adds and adjusts them (W1-W41), review pending |

Codex reviews of this bundle: r1 `20261013T163920Z-codex-omarchy-750d397d`; r2
`20261013T170941Z-codex-omarchy-d7391d2a`. The disposition of r3 is **pending**. Codex's signed review
records are not exported with this bundle. Any reference to them here is peer-relayed status, not
independently authenticated provenance.

Each draft ends with its explicit unknowns.
