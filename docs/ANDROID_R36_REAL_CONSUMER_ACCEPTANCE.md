# Devtool R36 — real-consumer hosted Android acceptance

Recorded: 2026-11-01 UTC. Devtool source commit used by both workflows:
`246f168ebdef6d9b25c194268c810c877d099fa3` (R36-R1).

| Consumer | Run | Consumer SHA | Hosted Gradle tasks | Result | Evidence |
| --- | --- | --- | --- | --- | --- |
| DustUI | [41258640269](https://github.com/Mikeyphw/dustui/actions/runs/41258640269) | `a006b3d2c422dcbd945c4f6b8eb36fec4455c327` | Nested `android/` project: `:app:lintDebug`, `:app:assembleDebug` | GitHub success; `BUILD SUCCESSFUL`; Devtool `LOCAL_JOB_SUCCEEDED` | [APK and native DAG](https://github.com/Mikeyphw/dustui/actions/runs/41258640269/artifacts/13064262860); [reviewed plan](https://github.com/Mikeyphw/dustui/actions/runs/41258640269/artifacts/13064093363) |
| MMRL | [41262381830](https://github.com/Mikeyphw/MMRL/actions/runs/41262381830) | `66ef3d94f2cbddb5c54de6f174d0676f8b5169db` | Root project named flavor: `:app:lintOfficialDebug`, `:app:assembleOfficialDebug` | GitHub success; `BUILD SUCCESSFUL`; Devtool `LOCAL_JOB_SUCCEEDED`; independent local package/signature verification | [APK and native DAG](https://github.com/Mikeyphw/MMRL/actions/runs/41262381830/artifacts/13065129727); [verification](https://github.com/Mikeyphw/MMRL/actions/runs/41262381830/artifacts/13065144673) |

MMRL `VERIFIED_DEBUG_NON_RELEASE`: package `com.mikeyphw.mmrl.debug`, APK SHA-256 `9ba028c77eb53889560b38a13c145278630693306aa25747eb54407608e760c8`, signer certificate SHA-256 `82cba1d5d49fb82dae24256a7c14aada6886b5831fada3a56a4f4c8fa32a6b26`.

**Acceptance:** R36 real-consumer execution of semantic hosted Android/Gradle nodes, nested project roots, named debug flavor, and bounded grouped debug tasks. This acceptance is specific to the successful source-bound debug runs and is not a production release gate.

**Not established by these results:** independently authenticated *downloaded* GitHub Actions artifact provenance (R35 importer not exercised here), device/instrumentation acceptance, production signer authority, reproducible builds, or release qualification. Do not merge these into one PASS gate.

MMRL's separate [release-seal run 41262382043](https://github.com/Mikeyphw/MMRL/actions/runs/41262382043) failed its debug unit-test stage on two old source-contract assertions; that failure does not invalidate the distinct hosted R36 debug-build evidence. Its remediation is tracked in the MMRL release-seal contract patch.
