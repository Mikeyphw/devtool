# Phase 47C-1 Android HUD Security Lint Hotfix

Tames the two security-adjacent lint families called out after Phase 47B without broad cleanup churn.

## Fixes

- Refactors `SupportBundle.redactionSelfTest(...)` so the redaction self-test still exercises bridge token, auth header, URL credential, secret assignment and private-key redaction, but does not keep complete credential-looking sample literals in source.
- Documents why `TaskerPluginReceiver` must stay exported and suppresses the lint warning at the receiver because runtime Tasker authorization is enforced in the receiver.
- Documents why `BridgeReceiver` must stay exported for Termux/root callbacks and suppresses the lint warning at the receiver because bridge token and action validation are enforced before accepting updates.

## Scope

This is a security-lint hotfix only. It does not change Git behavior, repo repair behavior, bridge tokens, command execution, or notification actions.
