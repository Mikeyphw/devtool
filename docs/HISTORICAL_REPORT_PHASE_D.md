# Historical report cleanup — Phase D

**Protected baseline:** `f25196a297d42bc3a2ff36a03aa0411f89fe9d46` (automated milestone verified; formal release not qualified).

The four report files listed in `HISTORICAL_REPORT_PHASE_D_PROVENANCE.json` had **zero exact-basename inbound references** in the V110 audited snapshot. A follow-up audit of Devtool's tracked runtime code and known report enumeration did not identify them as executable evidence or current maintained documentation requirements. They are now retired from the root, **not destroyed**: retrieve exact bytes with `git show f25196a:<file>` and verify the stored SHA-256.

No historical JSON lineage ledger, release evidence, compatibility contract, Android bridge, or installed-client fallback was rewritten. Source-bound DIST-G3 closure now validates current executable test-node contracts rather than historical Markdown file existence; the SSH/FRX crosswalk remains unchanged as a provenance locator.

**Limitations:** This is a repository-local consumer audit, not proof that no unknown external scripts, copies, or archived workflows expect those paths. The remaining 53 historically retained reports are **not approved for deletion** in this overlay. Preserve their provenance, review any references before further retirement, and rerun full current-source qualification on the next resulting commit.

Formal release remains blocked by `ENV-A06`, `ANDROID-A16`, `DIST-A01`, `DIST-A12`, `DIST-A13`, `TEST-A11`.
