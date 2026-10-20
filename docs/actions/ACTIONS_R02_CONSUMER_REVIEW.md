# ACTIONS R02 — Consumer-bound adoption review and acceptance boundary

## Implemented source contract

The existing `devtool actions adopt` command now reports source-bound identity
of the **consumer** in addition to the pinned Devtool runtime SHA. Read-only
review identifies the consumer Git commit and tree, selected job names, argv and
input-specification SHA-256 digests, declared host prerequisite profile, and
an independently reproducible `review_sha256` over that complete selection.
The review does not attest a GitHub run or authenticate an external publisher.
Dirty consumer worktrees appear as `consumer_worktree_clean=false`; writes
continue to refuse dirty trees.

Selection is checked again before writing. Explicit host requests other than
`go` or `python-shell` are blocked, contradictory declarations are blocked,
and the original A03 Python-shell YAML generator has not been changed.
The Go YAML still pins `actions/setup-go` to
`d35c59abb061a4a6fb18e82ac0862c26744d6ab5`, uses `go-version-file:
go.mod`, and uploads always-on non-release evidence.

## Live read-only second-consumer preview (operator-controlled)

Only after the R02 Devtool commit has been applied and actually pushed to
GitHub may it be used as a hosted runtime pin. To review a clean Skybind
consumer **without modifying Skybind**:

```sh
cd "$HOME/Code/skybind"
DEVTOOL_SHA="$(git -C "$HOME/Code/devtool" rev-parse HEAD)"
python3 -m devtool.actions_adoption --repo "$PWD" \
  --target skybind --workflow native --devtool-sha "$DEVTOOL_SHA"
```

Expected selection for the previously inspected Skybind main source:
`skybind/native` -> target-local `foundation-go`, with
`["bash", "scripts/devtool/check_native.sh"]` and `go.mod` input;
`ci_host_profile=go`. Skybind's source is evolving; **current** review is
authoritative and may refuse a changed workflow. No two-file write, commit,
remote dispatch, or hosted run occurs until separately approved.

Native post-install R02 source check (private receipt, no consumer mutation):

```sh
cd "$HOME/Code/devtool"
python3 -m devtool.actions_r02_acceptance --repo "$PWD" --record
```

To additionally review local Skybind, explicitly supply
`--consumer "$HOME/Code/skybind"`. This can be `BLOCKED` if the selected
consumer is dirty or no longer fits the exact A09 host contract.

## Historical producer/consumer matrix

| Witness | Source/run | Qualification |
|---|---|---|
| Cinderbox A07 hosted Android debug | `cb218cbae3e5eed5769ba784dd5d1fc747f40dd1` / GitHub `39336899057` | Hosted build success in its historical scope; not general A09 adoption or device/release qualification |
| Cinderbox A10 native default and explicit legacy fallback | Operator-reported both passed | Do not infer complete PR/push trigger parity, M3–M24 parity, artifact equivalence or automatic retirement from two green paths |
| Skybind native Go selection | Skybind `main` read-only inspection, 2026-10-20; source has `foundation-go` | Declarative host admission only; hosted source/run/attempt/artifact receipt PENDING |
| Devtool R02 | New source-bound producer tests + native apply pending | Only local code/source acceptance is in this overlay |

**Remaining mandatory R02 exit:** independent real GitHub hosted Skybind Go run
bound to verified Devtool pin, consumer ref/SHA, run and attempt, successful
selected `foundation-go` job and registered artifact ZIP/digest. Use the
existing Actions review/collect trust owners rather than introducing another
scheduler or a guessed run correlation. Android/SSH/root/signing and release
gates remain separate.
