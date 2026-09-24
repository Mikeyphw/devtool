# FRX-04 Boundary and Identity Gate 1 Report

## Delivered

- Added a dedicated, read-only FRX-04 federation qualification gate.
- Added `devtool federation preflight` with `gate1` alias and JSON output.
- Gate validates repository/config ownership and canonical owner identity.
- Every available enrolled child is resolved and then reloaded with parent
  config discovery disabled, proving child config isolation twice.
- Gate rejects parent/child identity collapse and duplicate child identity.
- Wrapper discovery and target-local job discovery are probed read-only for the
  owner and every available child.
- Synthetic EXO events prove first-class repository identity survives the
  authoritative execution store without executing repository jobs.
- Optional missing children remain non-fatal; required missing or ambiguous
  children fail the gate.
- Standalone repositories pass in explicit `standalone` mode.
- Doctor now exposes an FRX-04 `federation/gate1` qualification check.

## Validation gate

FRX-04 is the first federation validation gate. The packaged overlay requires
validation and must not be applied with `--no-validate`.

Validation covers federation identity/config/discovery/gate tests plus workspace
selection, wrapper resolution, target-local jobs, and EXO repository metadata.

## Scope boundary

FRX-04 does not create cross-repository workflow graphs and does not execute
child work. FRX-05 may only build federated plans on top of this sealed boundary
contract.
