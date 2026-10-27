# ACTIONS-A09 — Source-bound cross-repository host admission (producer stage)

## Why A09 exists

The accepted Cinderbox A07 installed-wheel DAG run `39336899057` uses a
repository-specific Java/Android host setup. A03's generic adoption generator
had no host toolchain admission: it could emit a workflow which checks out a
Go-based project without installing its required Go toolchain. Skybind's
reviewed `skybind/native` workflow has a `foundation-go` Bash job with `go.mod`
in `inputs`, and its existing Go CI already sets up Go. This is not a device
or release qualification and must never be promoted to either one.

## The bounded A09 producer change

- Select only direct target-local command jobs, reusing A03's authoritative
  project model. Refuse nested target/repository jobs, opaque commands,
  privileged tools, device/Android runners, release signer and root requirements.
- For a command invoking Go, a Go runner, `ci_toolchain="go"`, or a job
  explicitly listing `go.mod` in inputs, require an ordinary bounded go.mod
  with a `go` directive and `read_only=true` on that Go job.
- Provision `actions/setup-go` at exact immutable commit
  `d35c59abb061a4a6fb18e82ac0862c26744d6ab5` and
  `go-version-file: go.mod` ahead of `plan/preflight/invoke` for Go jobs.
- Preserve **byte-for-byte generated A03 workflow** for ordinary Python-shell
  workflows to avoid invalidating previously adopted source contracts.
- Continue to install Devtool from an immutable Git commit through pip, not
  via `PYTHONPATH` or an unverified floating wheel. The existing A06 CLI schema
  packaging proof remains the prerequisite.
- The generated workflow retains read-only repository permissions, always()
  evidence upload, exact producer/consumer sources, native Devtool EXO
  evidence and a non-release qualification boundary.

## How to use after Devtool A09 acceptance

From a clean Skybind branch with the reviewed Go `native` target-local workflow:

```sh
cd "$HOME/Code/skybind"
# Replace the variable with the ACCEPTED and PUSHED A09 Git commit, not A08.
A09_DEVTOOL_SHA='<accepted-40-hex-A09-commit>'
python3 -m devtool.actions_adoption \
  --repo "$PWD" --target skybind --workflow native \
  --devtool-sha "$A09_DEVTOOL_SHA"
# After review: repeat with --yes; then commit the 2 generated consumer files.
# Verify the committed generated source with --verify after adoption.
```

No workflow dispatch or code mutation is performed in the default preview.
A hosted GitHub run and exact artifact receipt verification are still required
before making a claim about the Skybind consumer. Device operations, Android
candidate signing, release qualification, paid protected CI, root and SSH worker
requirements remain separate. The historical original ACTIONS-09/G5 polyglot
matrix is already implemented; GitHub run `38437173945` passed 8/8
Python/Go/Rust/Java × Ubuntu/Windows cells and is not repeated here.

## Required follow-up evidence

Native Devtool A09 overlay acceptance and commit; GitHub branch SHA check;
Skybind review-only host profile (`ci_host_profile=go`); exact two-file consumer
adoption commit; opt-in hosted run; source/run/attempt/artifact qualification.
Do not claim any of these future steps have happened from source tests alone.
