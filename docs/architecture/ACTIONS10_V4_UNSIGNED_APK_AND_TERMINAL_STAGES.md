# ACTIONS-10 V4 — APK inspection and Termux stage display

## Scope

The GitHub build for run 38319546064 compiled successfully but failed before
artifact upload because the unsigned-APK ZIP inspector treated all case-distinct
member names as ambiguous. The original ZIP rule is replaced with rejection of
exact duplicate names, unsafe paths, symlink members, and case-fold collisions
for security-sensitive APK members. Case-distinct ordinary Android resources
and assets are permitted; apksigner still supplies the independent unsigned
signature check. Both CI manifest generation and local candidate import call the
same inspector. No protected release gate or signing policy is bypassed.

The Textual overlay-application presenter now tracks actual child stage evidence.
Its lifecycle stage ledger begins at Review, advances using canonical phase
start/finish events and narrowly recognized apply/validation/Git receipt lines,
and displays only stages with positive completion evidence as completed. A child
exit result does not silently mark unobserved steps successful. A failed validator
keeps Commit pending, and cancellation does not manufacture successful steps.
The canonical transaction and run receipts remain authoritative.

GitHub observations are retained into the final monitor snapshot, including
run/job/step identity, while the actual child exit code determines success.

## Limitations

Remote GitHub build/import/signing must be retested with a new run after pushing
this commit. The failed prior run has no downloadable unsigned candidate.
Terminal tests exercise the Textual UI in headless mode at Termux-like sizes;
actual Termux renderer verification remains an on-device acceptance gate.
