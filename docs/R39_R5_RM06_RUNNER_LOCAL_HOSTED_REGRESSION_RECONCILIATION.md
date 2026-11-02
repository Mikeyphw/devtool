# R39-R5 RM-06 hosted regression reconciliation

## Source and scope

Feature branch: `fix/r39-versioned-apk`.
Input source: `679d01f83d9d2028e9c83b9a2ed38da522570c02`.
Hosted full regression: `41387006505` (24 shards, 7459 tests, 3 failed).
Source gates: `41387009988` PASS.

This change updates only three regression tests that still asserted the
pre-migration RM-06 output. The production generator is intentionally unchanged.

## Correct contracts

1. A03 Python-shell adoption is bound to the current deterministic workflow
   digest `c30ef7b21bf321965bf23fbcdcc9fa5611f5f8b7767dd365e480b1fb60b8d8fc`.
   The historical digest `76382bc6c5ee0ec474e5b25ae0ce881f01cfefb6c79f84cabd5c07872320aca2`
   remains a historical reference and is **not** relabeled as current output.
2. The FC-06 NuGet shared-restore workflow uses a runner-executed step to
   write `NUGET_PACKAGES` and `PIP_CACHE_DIR` to `$GITHUB_ENV` before the
   Python setup/action execution, once per job.
3. The Windows RM-06/RM-09 projection asserts the same runner-local setup.
   Neither `NUGET_PACKAGES` nor `PIP_CACHE_DIR` may reference
   `${{ runner.temp }}` in job-level `env` (an invalid GitHub Actions context).

## Evidence limits

A Devtool overlay validation or Linux pytest pass does not imply source-bound
GitHub CI acceptance. After the overlay commit is pushed, run the exact new
commit through both `devtool-full-repository-pytest.yml` and
`devtool-r39-consolidated-source-gates.yml`. Keep native Termux npm, Android,
real hosts, signing, release and legacy-retirement qualification independent.
