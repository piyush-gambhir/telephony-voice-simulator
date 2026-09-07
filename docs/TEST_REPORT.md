# Additional test pass — 2026-09-07

The follow-up test pass exercises the installed package, production console,
and a real disposable MySQL 8.4.11 database in addition to the automated suite.
Carrier, LiveKit, and transcription credentials were excluded; no real phone
calls or production records were used.

**Final result: 391 tests passed**, including 17 real MySQL integration tests.
Production build, TypeScript, lint, Python wheel/source builds, release checks,
and Compose configuration checks also passed.

## Verified behavior

| Surface | Checks |
| --- | --- |
| Automated regressions | 391/391 passed with real MySQL enabled; 374 core tests plus 17 MySQL integration cases |
| Installed Python wheel | Imported outside the repository with editable source paths disabled; private audio and environment files excluded |
| Packaged commands | 32 AMD scenarios validate; 7 IVR and 4 PBX models execute; all 30 dealer cases pass |
| Credential-free operation | Benchmark dry-run creates a plan without placing calls or creating runtime state |
| Actual API processes | Health/catalog access, provider/endpoint/directory creation, AMD dry run, IVR simulation, graceful shutdown |
| Restart persistence | Resource IDs, directory configuration, and complete run payloads preserved across two independent API processes |
| MySQL | Real schema initialization, reopened repositories, rollback after relational conflicts, call/recording upserts, deletion tombstones, concurrent cancellation/claiming, bounded deadlock retries, controlled callback/deletion races |
| Production browser | All eight views at 320/390px; representative views at 768/1440px; keyboard tabs/search/dropdowns/submit; filtered JSON export; synthetic recording error/retry, decoding, playback/seek, byte-identical WAV download |
| Deployment configuration | Base/MySQL, ingress, and PSTN Compose overlays validate |

## Defects exposed

- MySQL can deadlock when cancellation and an incoming call claim the same
  queued run. Queue operations now retry the complete rolled-back transaction
  for MySQL deadlock error 1213, with a bounded attempt limit. Other failures
  remain visible.
- A late MySQL callback could recreate a deleted call after reading an outdated
  deletion check. Call and recording callbacks now use a locking read within
  an explicitly repeatable-read transaction. Controlled race tests cover
  connections whose default isolation is either repeatable read or read
  committed; deleted calls cannot be recreated by the paused callback.
- At 320px, the production Endpoints page overflowed horizontally and clipped
  action controls. The action row and minimum-width constraints are corrected
  and checked again against the production build.
- The README reported 28 dealer cases while the packaged suite contains 30;
  the documentation now matches the executable suite.

Real MySQL integration tests are retained in `tests/test_mysql_integration.py`
and enabled in CI with a disposable MySQL service. They can be rerun locally
using `SIMULATOR_TEST_MYSQL_URI` as described in [Contributing](../CONTRIBUTING.md).

All temporary API/browser servers and the disposable database are stopped after
testing. The downloadable recording and JSON fixtures contain synthetic data.

## Limits

Live carrier calls, remote LiveKit media, and external transcription were not
tested. Browser recording checks used synthetic WAV files. Docker image builds
were not repeated because the local Docker VM disk is full; the temporary
MySQL database used a memory-backed filesystem so existing Docker data did not
need to be removed. Python packaging and the production web build are tested
independently of Docker.
