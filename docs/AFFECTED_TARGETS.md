# Dependency-aware affected targets (MP07)

`--affected` turns Git changes into a workspace target scope. It is available on normal lifecycle commands such as `build`, `test`, `lint`, `validate`, `package`, and on graph-aware `doctor`/`preflight` analysis.

```bash
devtool validate --affected
devtool build --affected --affected-base origin/main --affected-head HEAD
devtool validate --group mobile --affected
devtool affected --explain
devtool affected --base origin/main --head HEAD --json
```

The selector is conservative. Devtool first identifies targets directly owning each changed path, then computes the reverse cross-target dependency closure. If a changed path cannot be assigned safely, every target in the candidate scope is selected rather than risking an incomplete build or validation.

## Ownership

A target owns files below its `root` automatically. Use `inputs` when files outside that root also affect the target:

```toml
[targets.api]
runner = "python"
root = "services/api"
inputs = [
  "../../packages/protocol/**",
  "../../schemas/openapi.json",
]
outputs = ["build/**", "dist/**"]
```

Patterns are interpreted relative to the target root. Reusable job inputs also contribute ownership for every target workflow that references the job:

```toml
[jobs.codegen]
runner = "node"
root = "tools/codegen"
command = ["npm", "run", "generate"]
inputs = ["../../schema/**"]
outputs = ["../../packages/generated/**"]
```

`outputs` are not change owners. They describe generated/write scope and are checked by MP08 preflight and later MP09 scheduling safety.

## Dependency propagation

Given:

```toml
[targets.web.workflows]
build = ["target:protocol#build", "runner:node#build"]
```

a change below the protocol target selects `protocol` directly and `web` through reverse dependency propagation. Group filtering is applied as a candidate scope after dependency propagation, so `devtool validate --group frontend --affected` can still select `web` when the changed owner is `protocol` outside that group.

## Git sources

Without a base revision, Devtool unions:

- unstaged tracked changes;
- staged changes;
- untracked files.

For CI/range analysis, pass `--affected-base`/`--affected-head` on lifecycle commands or `--base`/`--head` to `devtool affected`. With both revisions, Devtool evaluates `base...head`.

Environment fallbacks `DEVTOOL_AFFECTED_BASE` and `DEVTOOL_AFFECTED_HEAD` are supported by the selection engine when a caller does not provide revisions explicitly.

## Selection rules

- `--target` is mutually exclusive with `--affected` because one is an explicit singleton and the other is computed workspace selection.
- `--affected` may be combined with `--group` or `--all-targets` to restrict the candidate scope.
- target matrices remain separate from workspace affected selection.
- if no target is affected, lifecycle commands succeed as a no-op rather than falling back to the default target.
- ambiguous/unowned changes broaden selection instead of being ignored.

Use `devtool affected --explain` to inspect direct root/input ownership, reusable-job input ownership, dependency propagation, and conservative workspace fallback reasons.
