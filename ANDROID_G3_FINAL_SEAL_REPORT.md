# ANDROID-G3 — final Android environment/device/instrumentation seal

**Campaign position:** 59/70.  **Base:** qualified ENV-G3 V2 (`10430b3`).  **State:** candidate pending native-Termux qualification.

ANDROID-G3 is a pure final gate. The fixed-point audit found no Android-owned runtime gap after X01-X04, G1/G2, and the completed TEST/EXEC/DIST/LIFE/PRES adoption work. The broad pre-gate audit passed 299/299 tests.

The seal closes all 168 preserved M005 source promises (84 Android environment + 84 instrumentation) with per-row final dispositions and keeps generic CORE/ENV/EXEC/LIFE/PRES ownership exported rather than duplicating those authorities. The attestation cryptographically binds the source-disposition manifest and the already-qualified CORE-G3/ENV-G3 seals.

The seven-stage native-Termux workflow proves syntax, source dispositions, environment/device authority, instrumentation authority, X04 advanced adoption, downstream fixed-point consumers, and a final fresh-process chain. The fresh-process proof composes the existing G1 real Gradle/ADB environment proof and G2 instrumentation process proof with typed sharding/orchestrator/retry/quarantine/UI-Automator matrix evidence. It is observational and must not mutate repository state.

Next after successful qualification: **LIFE-G3 60/70**.

V2 hardening binds every per-row evidence reference by SHA-256, binds the aggregate evidence root into the final attestation, and rejects the construction-host bypass during authoritative native-Termux qualification.
