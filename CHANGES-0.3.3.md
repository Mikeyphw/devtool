# Devtool 0.3.3

- Automatic artifact commits now stage the complete devtool-managed artifact registry.
- `.devtool/artifacts.json` and all records under `.devtool/artifacts.d/` are included with the applied artifact commit.
- History records left by earlier failed, rolled-back, or manually extracted artifacts no longer leave the repository dirty after the next successful auto-commit.
- Artifact-scoped commits still exclude unrelated working-tree changes.
- The artifact-history lock now lives under devtool user state instead of inside the repository; legacy untracked `.devtool/artifacts.lock` files are removed safely.
