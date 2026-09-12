# Devtool exhaustive guides + warning-scope delivery report

This overlay delivers two generated, repository-local comprehensive guides:

- `docs/DEVTOOL_HUMAN_GUIDE.md` — operator mental model, workflows, safety/recovery guidance, Android operations, artifacts, validation, UI/integration, diagnostics, recipes, deep-dive documentation map, and an exhaustive parser-derived CLI reference.
- `docs/DEVTOOL_AI_AGENT_GUIDE.md` — deterministic agent operating contract covering discovery, selection, mutation, artifacts, validation, warning scopes, recovery, Android devices/signing, execution environments, structured output, and the same exhaustive parser-derived CLI reference.

`tools/generate_complete_feature_guides.py` regenerates both guides from `devtool.cli.build_parser()`. Its `--check` mode is suitable for CI. `tests/test_complete_feature_guides.py` asserts generated-file freshness and proves every current public command parser is present in both guides.

The same overlay fixes validation warning accounting. Devtool still parses and displays host/infrastructure warnings, but `validation.maximum_warnings` and expected phase outcomes now count only target warnings. Current non-target categories are low-storage notices, ADB probe notices, Gradle host/native-platform environment notices, Gradle infrastructure notices, and Gradle metaspace-pressure notices. Real compiler/lint/test/project warnings continue to consume the target budget; all errors continue to fail.
