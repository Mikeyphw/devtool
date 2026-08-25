# Artifact lifecycle

Build artifacts are recorded in `.devtool/artifacts/registry.json` with identity, hash, size, repository commit, timestamps, validity, and source metadata. Registration emits an `ArtifactCreated` architecture event.

The lifecycle commands are `artifact list`, `artifact inspect`, `artifact verify`, and `artifact prune`. File deletion during pruning is restricted to the managed `.devtool/build-artifacts` directory.
