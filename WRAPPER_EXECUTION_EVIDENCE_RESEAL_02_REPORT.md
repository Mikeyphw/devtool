# Wrapper execution-evidence re-seal 02

## Scope

This validation gate closes the remaining post-WRAP-09 audit gap: the original final seal checked configuration, launchers, docs, discovery, completion, help, resolution, and doctor, but did not itself prove WRAP-04 invocation-origin persistence or WRAP-05 single canonical re-entry behavior.

## Implementation

- Added `devtool.invocation_origin` as the shared WRAP-04 metadata contract used by the CLI and the final seal.
- Added `devtool.wrapper_execution` as the shared WRAP-05 execution bridge used by `wrapper exec` and the final seal.
- Preserved the existing CLI helper entry points while delegating them to the shared contracts.
- Changed `wrapper exec` to call `execute_wrapper_resolution(..., reenter=main)`, making the single canonical re-entry boundary explicit and directly probeable.
- Upgraded wrapper final-seal schema from 1 to 2.
- Added a read-only execution bridge probe that uses the real production bridge with an in-memory callback and requires:
  - exactly one re-entry callback,
  - callback exit-code propagation,
  - exactly one copy of each internal provenance control,
  - `invocation_source=wrapper`,
  - the correct wrapper command and schema,
  - no nested `wrapper` dispatch.
- Added a WRAP-04 persistence probe that publishes the same provenance through the real in-memory EXO `EventBus`/`ExecutionStore` and verifies the execution snapshot retains source, command, wrapper schema, and wrapper command.
- Runs the generic bridge/origin contract for every repository and command-specific probes for every non-interactive wrapper command. Commands requiring user values remain explicitly `skipped` while the generic production bridge contract stays sealed.
- Evidence payloads do not export raw execution argv, preserving hotfix-01 sensitive-value redaction.

## Audit-loop threat model

The remediation loop specifically tests that the seal turns red if:

1. the wrapper bridge invokes the canonical re-entry callback more than once;
2. wrapper provenance no longer persists as wrapper-origin EXO state;
3. workflow wrappers regress from canonical `invoke` dispatch;
4. a sensitive default enters execution evidence or JSON output;
5. the CLI and final seal drift onto different bridge/provenance implementations.

## Validation gate

This overlay is intentionally validation-required and rolls back on validation failure. Its artifact validator routes through Devtool's test selector with the complete WRAP-01..09/hotfix wrapper suite plus EXO store/finalization and completion/config compatibility coverage.
