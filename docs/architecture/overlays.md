# Overlay architecture

Overlay application is preflighted through `devtool.overlay`. `devtool overlay check` separates structural artifact validity from repository source-state validity and returns per-path conflicts. Rebaseable source-state conflicts can be repaired through `devtool overlay rebase` or `apply overlay --rebase`.

Artifact manifests use `.devtool-artifact.json` schema v2. Commit behavior is controlled only by `apply.commit.enabled`, `apply.commit.strategy`, and `apply.commit.message`; `--no-validate` does not disable manifest-driven commits.
