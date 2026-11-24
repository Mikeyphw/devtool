# GA-02 DT-20261124-30 · Native security coverage repair

This source-bound repair updates the **existing** `tests/ga02_native_validate.py`
runner to execute `tests/test_ga02_live_registry.py`,
`tests/test_ga02_federation_source_guards.py`, and its new guard
`tests/test_ga02_validator_inventory.py` during native and hosted GA-02 source
acceptance. It does not change installation tokens or production App authority.

- Preimage: `Mikeyphw/devtool`, PR #48, `b6e6550e0d8e42f4d0009d56a81dcbdb35dc9d0f`
  tree `7510e3969cbd678fe3f708a6fb92ba207f60238a`; native validator blob
  `4b1f0681f63494682c294f1c38d857faa76a5122`.
- Why: green CI24 did not prove that the **focused native GA-02 validator**
  itself required these security suites. This fixes the inventory, not a token
  permission or simulated production security result.
- Native delivery: verify exact preimage; check schema/guard; atomic native apply,
  commit and new session; update already-open PR #48 by normal fast-forward push
  and PR observation; check required CI at **new head**; separately qualify CI24.
- Do not replay the original GA-02 transaction `20261123T235925Z-01678df641fd`,
  history `a9eae8952c4d14dc882101799334eda3`, or session
  `686c8049aa36465aaf41caa0dce833a1`. Preserve original receipts.
- Acceptance: live App grant, denial/revocation, wrong-installation and
  optionally destructive write scenarios require separate target-specific
  authorization and independent evidence. The repair is not full GA-02 acceptance.

Bug ledger: `DT-20261124-30`; integrated roadmap overlay `GA-02`, batch B0/B2.
