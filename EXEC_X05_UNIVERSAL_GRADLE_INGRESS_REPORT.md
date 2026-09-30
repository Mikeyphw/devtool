# EXEC-X05 — Universal Devtool Gradle ingress convergence

Campaign position: **35/70**
Gate window: **2/3** (`ENV-X05 -> EXEC-X05 -> GRADLE-TAKEOVER-G1`)
Owning scope: Devtool-owned execution ingress, canonical lowering, EXO/RER identity convergence, and RUN/EXO takeover evidence.

## Merge-window decision

The required next-three inspection covers **EXEC-X05 35/70**, **GRADLE-TAKEOVER-G1 36/70**, and **ANDROID-X01 37/70**. They remain separate. EXEC-X05 is implementation, GRADLE-TAKEOVER-G1 is the authoritative whole-window proof gate, and ANDROID-X01 begins the next major scope.

## Delivered boundary

EXEC-X05 converges Devtool-owned Gradle entry paths on the ENV-X05 broker without creating another Gradle planner, scheduler, cache, receipt store, or execution truth source.

- `devtool.gradle_ingress` is the execution-boundary guard for representable raw `gradle`, `gradlew`, and `./gradlew` `CommandSpec` vectors before reusable identity is finalized.
- Raw work is lowered through `broker_gradle_invocation()` and the existing ENV-X03 `GradleInvocationRequest` / canonical planner. Disabled/out-of-scope takeover remains behaviorally inert and does not resolve a real launcher early.
- Typed JVM and Android work now enter the broker directly with `require_plan=True`; the canonical planner still runs exactly once. An already-planned `CommandSpec` is evidence-retagged but never planned a second time.
- CLI runtime provenance is carried on `ProjectConfig` so wrapper-originated and direct invoke work can be distinguished without process-global state. Nested execution records an origin chain while the current typed origin remains explicit.
- Workflow lowering occurs after workflow execution metadata/destination is known and before workflow phase keys/RER annotation. TestSpec, validation and artifact-validation raw commands cross the same guard.
- Android canonical planning uses the same broker. When takeover actively manages the launcher, the Android builder receives the broker-resolved launcher instead of silently re-selecting a raw Gradle command; instrumentation prebuilds reuse the same Android canonical path.
- SSH/federation-scoped specs derive takeover destination from their scoped execution metadata. Deferred destinations retain destination-qualified launcher identity instead of borrowing host PATH state.
- `PROBE` and `CONTROL` stay non-WORK even when a typed caller requires a canonical work plan for normal execution.
- Known nested/opaque shell Gradle programs are never described as intercepted. Manage/observe retains the original shell program with explicit unrepresentable coverage; strict mode rejects it.
- Raw lowering preserves original argv/cwd as evidence, preserves the executable spec environment and cwd, and carries the canonical physical Gradle arguments into execution.
- Takeover **receipt identity** remains origin-aware for evidence. A separate **origin-neutral semantic identity** uses physical Gradle semantics, effective policy, launcher and destination so equivalent work does not receive a different RER environment domain merely because it entered via wrapper/invoke/workflow.
- Terminal RUN/EXO task evidence projects the existing takeover object, receipt ID, semantic identity, origin/origin chain, launcher identity and destination identity. No second takeover history/store was added.
- `run_specs()` has a defensive library-call convergence boundary. If a caller already attached an RER fingerprint before reaching that safety net, convergence refreshes it after canonicalization rather than retaining stale raw-launcher identity.
- Canonical execution continues to own interruption, cancellation, exit status and diagnostics; takeover does not synthesize a competing process result.

## Promise coverage

This implementation owns **GT-P013 through GT-P026** from `DEVTOOL_GRADLE_TAKEOVER_PROMISE_LEDGER_20260929.json`:

- ingress guard before EXO/RER identity;
- devtoolw/direct invoke/workflow convergence;
- no typed double planning;
- TestSpec/validation/artifact/Android/instrumentation coverage;
- SSH/federation origin + destination preservation;
- internal already-planned handling;
- argv/cwd/environment preservation;
- cross-surface semantic identity convergence;
- truthful opaque-shell handling;
- RUN/EXO takeover evidence;
- canonical cancellation/signals/exit/diagnostics.

## Targeted implementation validation

The repository declares `roadmap-exec-x05` and wrapper command `exec-x05`. On this desktop host, the repository wrapper correctly refuses its configured `native-termux` preflight, so local qualification runs the same declared workflow through Devtool with an explicit host execution override:

```text
python -m devtool -r . --execution-environment host invoke roadmap-exec-x05
```

The final replay result is recorded in `EXEC_X05_PROMISE_LEDGER.json`. Additional focused retained checks cover the Android instrumentation canonical planner boundary plus wrapper/core invocation provenance.

This is intentionally implementation-owned validation. It does **not** substitute for GRADLE-TAKEOVER-G1's authoritative real-process, recursion, convergence, cancellation and evidence qualification.

## Next boundary

Next: **GRADLE-TAKEOVER-G1 — 36/70**, gate-window **3/3**. It must audit the complete ENV-X05 + EXEC-X05 window and close all exported obligations before the campaign advances.

Remaining before the next major scope: **1 overlay**.
Next major scope: **ANDROID-X01 — 37/70**.
