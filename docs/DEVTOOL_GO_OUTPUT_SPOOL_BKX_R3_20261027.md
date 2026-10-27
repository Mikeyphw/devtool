# Go JSON subprocess output transport — BKX WaitDelay candidate

## Scope and source

Base: Devtool feature `feature/rmp01-security-remediation`, commit `3ddb1c3525ddabcce13f1d86caabc18beef7c214`, tree `13b3cf2b338d8340153ad806bc57056a0798a963`. The unsuccessful FC08 HOST-R1R6 native transaction has **not** been stacked. This change modifies only the `progress_parser=go-test-json` transport in `runners.py`, adds `go_output_spool.py`, strengthens Go JSON completeness in `go_test.py`, adds focused regression tests, and updates the cumulative ledger. The Go validation DAG in `project_runners.py` remains untouched.

## Evidence and cause discrimination

BKX R2 transaction `af27fac12cd44663ae2b5dc20e753ccf`; R3 `dd90c585b20a4ca993e5b57a4d41fcea`; disposable archive `BKX-DEVTOOL-EXACT-20261027T041137Z.zip`: original reported `exec: WaitDelay expired before I/O complete` with two package I/O errors after assertion-level successes. These are *historical evidence claims from the operator's handoff*, not acceptance receipts reproduced in this workspace. In Go's `os/exec`, WaitDelay errors can follow a command exiting zero without closing output pipes; inherited descriptors are a real alternative to presentation backpressure. No cause is asserted based only on this report.

## Delivered behavior

* Dedicated producer thread solely drains raw bytes from the child stdout into a private 0600 file; no formatting, logging or UI callbacks in that thread. A separate file descriptor and positional reads allow the main thread to render at its own rate while preserving bytes/order.
* Bounded output file with hard default capacity of 256 MiB, override `DEVTOOL_GO_OUTPUT_SPOOL_MAX_BYTES` (1024 bytes to 4 GiB). Exceeding capacity makes the Go test stage fail with return code 75, terminates the controlled process group and preserves a machine-readable transport error. No output dropping is accepted as success.
* Existing GoTestJSONStream parses in main thread and retains package/test identity, failure locations and rerun commands. Malformed JSON-looking events or started packages lacking terminal package outcomes cannot become a zero-exit success.
* Existing process-group timeout, tasker cancellation, idle timeout, supervisor, memory guard and heartbeat remain on the main thread. An EOF not observed within the bounded grace after the main child exited is reported as possible inherited stdout descriptor; no orphan identity is claimed without process inspection evidence.
* Machine-readable `result.metadata.go_output_transport` includes bytes drained/spooled/consumed, maximum backlog, EOF observed, raw child exit, completion timestamps, transport error, shutdown reason and configured capacity. Raw spool is deleted after completion and not attached to receipts.
* Windows uses a polling `PeekNamedPipe` branch because `select` cannot watch Win32 anonymous pipes. Windows/Android/Termux and BKX end-to-end authority remain unqualified until independent real-host acceptance.

## Promise-by-promise provisional audit

| Promise | Local implementation | External authority |
|---|---|---|
| Prompt Go stdout draining independent of slow presentation | `GoOutputSpool._produce`; deliberately slow-renderer test | BKX R3 transaction OPEN |
| Bytes/line/event ordering | Committed offsets and positional reads; concurrent ordering test | Real BKX OPEN |
| Private bounded storage and cleanup | `mkstemp`, 0600, hard quota; tests | Native Termux OPEN |
| Capacity/read/renderer errors never silently succeed | rc 75 and failure diagnostics; regressions | Native Termux OPEN |
| EOF vs inherited descriptor | Independent EOF flag, bounded post-exit grace, process-group termination; descendant regression | Root cause still UNDETERMINED |
| Cancellation/timeout without reader leaks | Existing watcher plus stoppable producer; cancel/renderer tests | Native Termux OPEN |
| Go package/test/failure reporting | GoTestJSONStream retained; existing failure suite passes | BKX R3 OPEN |
| Full Go restore/build/test/vet DAG | `GoRunner.build_validation_specs()` unchanged | BKX disposable Devtool transaction OPEN |
| Full Devtool source validation | Scoped Python suites passed locally | Full repo and 24-shard hosted G1 OPEN |
| Original rollback and transaction receipt | Source-bound overlay, native Devtool transactional test pending real Termux | Native Termux OPEN |

No BKX runtime implementation, chroot-ctl, API-04, root bridge, rclone, Go WaitDelay setting, or permanent test `-count=1` change.
