# FC08-HOST-R1 — Actual hosted Devtool workload gate

## Source / authority

Base `e09057529dc9016bc514ccfb43e45b0b978625b7`, tree `337d4ef8bb06a8a700bce469a869c6bcba256dc3`.
This source-bound overlay changes only the hosted evidence infrastructure, native fixture
runner, fail-closed summary, current-contract tests, cumulative ledger and the hosted
`cryptography` CI dependency. It does not split the repository (RM19 is cancelled).
No signing keys, tokens, credential publishing or privileged repo permissions are needed.

## Promise-by-promise audit

| Promise | Implemented path | Current qualification |
|---|---|---|
| G5 fixture matrix preserved | [run 40426743366](https://github.com/Mikeyphw/devtool/actions/runs/40426743366) | PASS, bounded fixtures |
| Exact source / tree / attempt | `fc08_host_workloads.identity`, summary verifier | Local synthetic tests PASS; hosted OPEN |
| Hosted DAG evidence | four-needs workflow with negative CLI return-code and fail-closed artifacts | Source delivered, repeated/permission cases OPEN |
| Linux real provider exercise | Devtool Python, Go, Node, Rust, Maven, CMake, Meson + uv/Poetry/PDM lock/run | Source delivered; hosted and ABI/lock adversarial OPEN |
| Windows .NET/MSVC evidence | VS dev cmd, Devtool .NET, real locked restores, xUnit TRX/Cobertura, NuGet, publish, CMake MSVC | Source delivered; hosted OPEN |
| macOS native workload | Native Devtool CMake build/CTest + rejected Windows-only provider | Source delivered; hosted OPEN |
| Fail-closed SHA receipts | `fc08_host_summary.py` and `fc08_host_capture.py`, expected checks, no device/release promotion | Unit tests PASS; independent content audit OPEN |
| G1 Ed25519 skip | Add bounded cryptography to 24-shard workflow | Requires fresh full G1 rerun on new source |
| Termux ARM64/Android/real consumers | Different authority; intentionally not in HOST | OPEN |
| FC09 168-promise audit | Preserve 160 active / 8 cancelled RM19 | OPEN until proper evidence |

**No provider lane is qualified just because this source code is installed.** Even a
passing workflow yields `HOSTED_REAL_WORKLOAD_EVIDENCE_CAPTURED_PENDING_INDEPENDENT_REVIEW`.
The data capture utility must verify the GitHub API run/job/artifact source tuple and
actual downloaded JSON before any lane can be marked independently attested.

## Acceptance plan

1. Apply to exactly the clean attached source, run deterministic contract validator;
   rollback on failure. Create a single source commit.
2. Push that commit to the existing feature branch, add the new workflow to main
   *only if absent* and with exactly matching reviewed bytes (or refuse). Never
   merge the feature branch merely to register workflow dispatch.
3. Run `.github/workflows/devtool-fc08-host-workloads.yml` on the new feature commit.
   It requires all four lanes and fail-closed summary to pass.
4. Download four raw lane JSONs and summary; independently match SHA-256 and
   GitHub's authenticated run/attempt/job/artifact digest before changing gate
   disposition. This is evidence-only, not release.
5. Rerun full 24-shard Python regression on the changed source to verify the
   skipped Ed25519 test actually executed, then audit FC08 gate checklist.
6. Separately execute native Termux, Android signing/device and real
   Telegram/Studyflow/BKX consumer qualifications. FC09 only after required
   external evidence or an owner-approved explicit scope amendment.

## Native transaction guardrails

The overlay installer refuses dirty, detached or unexpected HEAD/tree. It
requires a configured Git identity and verified bytes. No automatic reset,
repository split, destructive cleanup or signing secret. The consumed artifact
can be deleted only on Devtool's successful atomic apply; failures retain
transaction and evidence for diagnosis.


## R1R2 native Termux collection fix

The first operator apply (`5843698efef74683919f8e955fff8abc`) failed at pytest collection because `from tools import ...` chose the installed Hermes Agent `tools` package instead of the Devtool repository. R1R2 imports sibling test fixtures through the resolved repository `tools/` path and tests each module origin. The eight other R1 payload paths and the hosted workload semantics are retained. This is a replacement on the exact clean R1R4 source, not an incremental apply. The operator's full transaction result and actual source cleanliness must be verified before installing. Native and hosted G1/FC08 gates stay open.
