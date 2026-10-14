# Devtool hosted CI run 38517845080 — source-bound repair 01

Original source: `7a06153745ca5180112930f1db260d4c50ea8b8e`

This overlay is a partial remediation, **not** a G8 release qualification or a clean full-repository regression verdict.

## Root causes covered

1. Federation resume checkpoint: preserve never-executed `blocked`/`cancelled`/`pending`/`skipped` child rows without manufacturing a child run ID; require IDs for executed children, retain duplicate and unknown-step guards, never reuse non-success evidence.
2. Standalone `devtool commit`: previously referenced local `config` before assignment. For an unconfigured Git folder, use the default signing mode; for a configured project, resolve its declared signing policy.
3. Android signing profile secret-source display: missing optional file/env/fd attributes now mean no configured secret source, rather than an attribute exception. Does not accept unsigned builds or synthesize secrets.
4. CI hosted test dependency: install `pexpect` for interactive PTY tests.

## Verification and remaining work

43 focused regression/negative tests passed locally against this exact source. Other failures in the original 100-case report are not silently approved. Historical release seals, toolchain-dependent checks, and source-provenance contracts remain fail-closed. Full hosted rerun required.
