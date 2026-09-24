# FRX-07 — Wrapper + Target-Local Job Federation Bridge

Status: implemented

FRX-07 connects federation orchestration to the two established repository
public contracts without bypassing either one.

Delivered:

- `devtool federation exec <repo> -- wrapper <args...>`;
- `devtool federation exec <repo> -- job <reference>`;
- `--plan` resolution for either contract without execution;
- child-only wrapper resolution and canonical WRAP one-time re-entry;
- simultaneous WRAP invocation provenance and FRX-06 parent/child lineage;
- redacted wrapper argv on federation parent evidence;
- direct target-local/repository job resolution into ordinary `CommandSpec`s;
- strict `<target>:job:<name>` ownership with no repository fallback;
- child EXO run ownership and direct-job failure-code propagation;
- no parent `--repo`, target, group, config, or wrapper routing leakage into the
  child contract.

FRX-07 remains a non-gate overlay and is intended for `--no-validate`.
