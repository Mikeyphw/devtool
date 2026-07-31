# Artifact History Finalization Hotfix

## Problem

After all validation phases passed, automatic finalization always included `.devtool/artifacts.json` in the path-scoped Git staging list. Repositories that did not have or track that optional registry failed with Git exit 128:

```text
fatal: pathspec '.devtool/artifacts.json' did not match any files
```

## Fix

`_artifact_commit_paths()` now includes an artifact-history path only when:

1. the path currently exists, or
2. Git already tracks the path, including a tracked deletion.

Ignored and untracked Devtool runtime history remains local. Declared artifact payload files retain their existing force-add behavior.

## Regression coverage

- missing and untracked registry is omitted;
- existing and unignored registry is included;
- tracked registry deletion is included;
- ignored runtime history remains excluded;
- complete history reconciliation remains covered.
