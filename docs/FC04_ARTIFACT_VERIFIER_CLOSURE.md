# FC-04 — RM12/RM13 artifact verifier integrity

Source baseline: clean post-FC-03 Git tree `d1efec65f5c35afaf8e640e33e5d8262bb952e72`; original user commit `2ab62056602446d03754a922061c0f4cf8015f76`.

## Promises and implementation

| Original promise | Verified change | Regression evidence | Local status |
|---|---|---|---|
| RM12-T3 | Nested desktop JARs pass the same ZIP limits, manifest/classpath, class-structure and signing-integrity checks as standalone JARs. Launcher is the reviewed `bin/start`, exact referenced JAR, executable regular file. | `tests/test_fc04_artifact_integrity.py`, `tests/test_rm12_jvm.py` | SOURCE_IMPLEMENTED_LOCAL_TESTED |
| RM13-T3 | Native ZIP must contain >=1 recognized ABI-verified ELF, PE or Mach-O member; all detected native members must match the declared ABI. Invalid purported native members refuse. Optional byte-receipt identity is verified. Structure-only ZIPs reject instead of asserting ABI qualification. | `tests/test_fc04_artifact_integrity.py`, `tests/test_rm13_cpp.py` | SOURCE_IMPLEMENTED_LOCAL_TESTED |
| RM05 compatibility | ZIP/JAR traversal, symlink, duplicate/ambiguous entry, encrypted entry, decompression and checksum/size limits remain bounded. | Adversarial fixtures and selected compatibility suites | SOURCE_IMPLEMENTED_LOCAL_TESTED |

## Threat and acceptance boundaries

* Byte-structure evidence is not signer identity authentication, software supply-chain trust, uploaded artifact authenticity or release authority. Receipt fields explicitly keep `release_qualified=false`.
* Signed nested JAR verification uses a private ephemeral file for `jarsigner -verify -strict`; absence/failure of the tool is a refusal, not silent downgrade.
* Class structure verification parses constant-pool encodings and member-length framing; it is **not a Java VM verifier** or bytecode execution proof.
* ABI recognition validates file signatures/header, supported machine IDs and nonempty native members. It is not full link/load/execute validation on every target OS. Hosted compiler and actual consumer receipts remain OPEN_EXTERNAL_EVIDENCE.
* The original RM13 text-only-ZIP positive test is corrected. Production C++ packaging includes a `byte-receipt.json`; when present, its expected ABI and SHA-256 must match the sole verified native payload.
* FC-05 RM20 onboarding completeness and FC-06 NuGet sharing are separate promises. Do not reopen cancelled RM19 repository extraction.

## Acceptance requirements

1. Deterministic adversarial regression and RM12/RM13 compatibility tests pass, including real compiler/JDK where installed.
2. Source-bound overlay applies to the exact clean post-FC-03 tree. No `--keep-artifact`, no forced conflict bypass.
3. Real Devtool validate and apply tests pass; wrong committed source, dirty worktree and injected validator failure must refuse/rollback with identical HEAD/tree/index/worktree state.
4. Termux installed-tree and real consumer/hosted artifact receipts remain separate. This document does not confer those gates.
