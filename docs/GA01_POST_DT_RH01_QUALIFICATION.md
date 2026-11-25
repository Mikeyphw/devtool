# GA-01 — Qualification after DT-RH01 / PR #50

Source reconciliation must contain the original GA-01 branch head `f4290fb1cf34fd830608418870d200dd3be25bd5` and merged main `7543dc19caa9a151bbc38ffeaa36d0a01b8090de`. GA-02 additionally requires the newly qualified GA-01 tip as its merge parent, not the historical session.

The native validator now includes the post-merge App push scope, CI24 failure-observation and native delivery-contract regressions. These tests are mandatory; no local Gradle, live writes, or test suppression. The native ZIP binds the exact reconciled Git tree and commit, preimages, postimages and current main base. A distinct native transaction and session must be used.

PR #46 must be observed, never replaced. Required CI and CI24 must qualify the resulting exact head. Read-only App scope and adverse tests are distinct from live disposable-repository write/revocation qualification. GA-02 relationship-policy qualification requires an owner-approved digest-pinned policy and current source. Merge and formal release remain independent.
