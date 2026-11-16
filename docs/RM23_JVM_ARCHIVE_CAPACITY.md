# RM23 — bounded Kotlin/JVM archive inventory

StudyFlow `:core:model:jar` is an ordinary compiled Kotlin/JVM output and can
contain more than the RM12/RM21 historical 256-entry capacity. RM23 raises the
**entry-count capacity to 4096** for both the RM12 verifier and RM21's
independent-consumer precheck. This does **not** suppress JVM archive integrity
checks: duplicate/case-insensitive names, absolute/traversal paths, encrypted
entries, symlinks, corrupt ZIP data, invalid class structures, unexpected
signatures, per-member (16 MiB) and total expanded (128 MiB) size limits remain
fail-closed. The verifier still refuses archives containing 4097+ entries.

This is source-only policy work. Its tests use synthetic, structurally valid
JVM class bytes and require no local Gradle/Android execution. A new hosted
StudyFlow workflow records bounded real JAR inventory **before** verification
and continues to reject noncompliant archives. A passing verifier is not a
release, signer, or independent JVM-consumer qualification receipt.
