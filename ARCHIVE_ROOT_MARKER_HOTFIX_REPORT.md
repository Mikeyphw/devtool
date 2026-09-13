# Archive root marker hotfix

Roadmap position: Overlay 6 of 15 is paused by a compatibility regression found after Overlay 5.

## Problem

Overlay 5 correctly hardened archive path validation, but it treated harmless directory root marker entries such as `.` or `./` in tar archives as an unsafe empty payload path. Some valid overlay artifacts produced by `tar -czf artifact.tar.gz .` contain that directory marker before the real payload members. Devtool therefore failed before apply with:

```text
RuntimeError: Unsafe archive path: empty path
```

## Fix

`list_archive_members()` now ignores directory-only archive root markers before canonical path validation. Empty/root non-directory members, traversal, absolute paths, backslashes, links, devices, duplicate normalized paths, and file/child collisions still fail closed.

## Regression coverage

Added tests that prove:

- a tar archive with root directory marker `.` plus a real payload member lists only the payload member;
- an empty non-directory archive member still raises `Unsafe archive path: empty path`.

Focused validation run:

```text
PYTHONPATH=$PWD pytest -q tests/test_rem05_transactions_archives_rollback.py -q
19 passed
```
