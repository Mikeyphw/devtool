# RM22 — Hosted native child environment preservation

`devtool.actions_rm21_hosted_native._child_env()` previously filtered `RUNNER_OS` while forwarding `CI` and `GITHUB_ACTIONS`. This blocked consumer builds whose settings require the GitHub-hosted runner platform to be present, including StudyFlow's `settings.gradle.kts`.

This patch forwards the recognized nonsecret runner value `Linux`, `Windows`, or `macOS`. It **does not** forward credentials, arbitrary inherited variables or `PYTHONPATH`; it does not authenticate GitHub Actions and does not weaken source, signature or artifact admission. No local Gradle execution occurs. The provider still requires checked-in dependency verification metadata for RM12 JVM acceptance.

Merge this Devtool source improvement separately, and pin the resulting merged commit before subsequently running StudyFlow's independent RM21 JVM acceptance.
