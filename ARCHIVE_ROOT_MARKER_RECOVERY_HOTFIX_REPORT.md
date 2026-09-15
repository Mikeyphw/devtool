# Archive Root Marker Recovery Hotfix

## Problem

Some tar/zip producers include archive-root directory marker entries such as an empty name, `.`, or `./`. Devtool preview and artifact validation already ignored those harmless directory markers, but the apply-time extractor revalidated archive members independently and failed with `RuntimeError: Unsafe archive path: empty path`.

That meant a structurally acceptable overlay could pass manifest/preview checks and then fail during extraction, triggering noisy rollback/recovery behavior.

## Fix

- Apply-time zip extraction now skips harmless root directory markers before path canonicalization.
- Apply-time tar extraction now skips harmless root directory markers before path canonicalization.
- Empty non-directory members still fail closed as unsafe payloads.
- Repeated rollback/recovery calls against a transaction path that has already been cleaned up now return an idempotent no-op result instead of surfacing a misleading `Invalid transaction JSON` error.

## Scope

This fixes future Devtool applies. Existing broken third-party overlay archives do not need to be repacked when the only bad member is a harmless archive-root directory marker.

## Validation

Added regression coverage for tar root markers, zip root markers, unsafe empty non-directory members, and idempotent rollback after transaction cleanup.
