# Patch Summary

This full Devtool snapshot adds the interactive validation cockpit and wizard.

## Entry points

```bash
devtool menu validation
devtool validation-menu
devtool vm
```

## Highlights

- state-aware fzf menu with bat previews and a complete numbered fallback;
- guided one-shot, collect-all, sequential, watch, matrix, Wine, artifact,
  bundle, and apply-time validation flows;
- revision-guarded session controls, safe retry, plan acceptance, live logs,
  reports, reset, finalization, and explicit keep-or-rollback abort;
- all structured test selectors and Gradle task discovery;
- resource profiles plus ephemeral workers, CPU, heap, memory, priority,
  parallelism, build-cache, and daemon overrides;
- runtime/device guards, failure policies, HUD/output controls, canonical
  command review, editing, clipboard, dry-run, and TOML recipes;
- zsh completion, README, manual page, implementation guide, and regression
  coverage.

## Validation

```text
167 passed, 30 subtests passed
```

The validation-focused gate covers the new interactive suite, CLI behavior,
shell completion/manual parity, durable sessions, and the stable control API.
The untouched input snapshot reproduces the unrelated failures seen in the
broader historical suite.
