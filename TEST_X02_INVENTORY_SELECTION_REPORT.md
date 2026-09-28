# TEST-X02 — Stable Inventory, Lanes, Fixtures, and Deterministic Selection

## Roadmap boundary

TEST-X02 is intentionally standalone. The preceding TEST-G1 milestone remains a distinct authoritative gate, TEST-G2 remains the next distinct gate, and TEST-X03 owns performance/runtime evidence rather than inventory and selection semantics.

This overlay implements the complete TEST-X02 ownership boundary without invoking Gradle. Under the V18 validation policy, the implementation overlay validates its changed Python/test-system boundary directly; Gradle-related validation remains reserved for milestone/final gates.

## Delivered contracts

### Canonical test identity and inventory

- Extended frozen `TestSpec` with durable check identity separated from the current executable selector.
- Added historical aliases/campaign IDs, behavior/domain, lane membership, resources, parallel-safety classification, required tools/capabilities, budget class, public-vs-white-box contract level, groups, dependencies, and metadata.
- Added a canonical `TestInventory` that validates check IDs, aliases, and group membership and exposes an inventory digest.
- Added repository-owned `devtool-tests.toml` as the durable registry instead of encoding roadmap identity in pytest node IDs or shell command strings.
- Inventory compilation merges durable registrations with discovered pytest tests. Unregistered tests remain discoverable but are explicitly reported as selector-derived rather than silently becoming roadmap-stable identities.
- Added explanation output so callers can distinguish durable identity from selector-derived discovery.

### Lanes, resources, and pytest policy

Canonical lanes are:

- `fast` — pure/unit/contract checks,
- `integration` — real Git/process/PTY/repository/session boundaries,
- `external` — real host/tool/environment dependencies,
- `system` — broad milestone/seal qualification,
- `full` — the union of executable lanes.

The pytest runtime now consumes the repository inventory before filename/marker fallback when classifying collected items. `--devtool-lane` therefore performs real collection-time deselection using the same durable policy as inventory/selection planning.

Strict pytest marker policy was expanded for lane/resource/parallel-safety/tool/capability/contract/dependency/budget metadata while retaining strict marker/config behavior.

### Immutable deterministic selection

Added an immutable canonical `TestSelection` carrying:

- selection ID and digest,
- requested lanes/checks/groups,
- resolved stable check IDs and selectors,
- explicit exclusions with reasons,
- resource requirements,
- serial constraints,
- configuration provenance,
- deterministic shard plan.

Selections resolve aliases and stable roadmap groups, reject unknown lanes/checks/groups, and never mutate after construction. Shards are assigned by stable check identity rather than discovery/file order, making the plan repeatable across equivalent inventories.

### Stable roadmap groups

`devtool-tests.toml` introduces stable groups including:

- `roadmap.test.foundation`,
- `roadmap.test.g1`,
- `roadmap.test.g2`,
- `roadmap.test.system`.

The TEST-G2 group is present before the gate itself so the next gate can consume a stable declared check group instead of reassembling command lists ad hoc.

### Fixture factories

Added reusable behavior-level factories under `tests/support`:

- `RepoFactory` for deterministic isolated repositories/files,
- `GitRepoFactory` for deterministic Git identity, commits, branches, and worktrees,
- `ValidationSessionFactory` with unique repository/session state roots,
- `ArtifactFixtureBuilder` for deterministic overlay ZIP fixtures and hashes,
- `StableCommandBoundary` for behavior-centric command mocking without binding tests to private implementation details.

These are exposed through central pytest fixtures.

### Declared-test and CLI integration

- Existing `[[test]]` configuration parsing now carries the extended `TestSpec` policy fields.
- The declared-test bridge transports the same policy into command metadata instead of dropping it.
- Added `devtool pytest inventory` for lane/group inventory inspection and durable-check explanation.
- Added `devtool pytest select` for immutable selection planning and deterministic shards.
- JSON output uses Devtool's existing canonical machine envelope rather than introducing a second JSON contract.

## Validation evidence

Targeted non-Gradle validation performed against the post-TEST-G1 state:

- Fast X02/selection/declaration/pytest-policy lane: **39 passed**.
- Integration TEST-X01 + isolated Git transaction lane: **28 passed**.
- Integration validation truth/reporting lane: **24 passed**.
- System TEST-G1 authoritative proof lane: **5 passed**.
- Mixed real collection proof (`fast` X02 plus `system` G1): fast tests ran while the five G1 system tests were deselected, proving collection uses inventory policy rather than only modeling lanes in JSON.
- Python compilation: passed.
- `git diff --check`: passed.

A broader config/CLI replay produced **167 passed, 4 failed**. Each of the four failures was replayed unchanged on the untouched post-TEST-G1 baseline and is therefore pre-existing rather than introduced by TEST-X02: three old mocked apply/commit call sites reject the existing `tasker_progress` keyword and one old path observes an unbound `config`. They are not folded into TEST-X02 or hidden by its selection policy.

No Gradle command was run by this implementation overlay.

## Gate boundary

TEST-X02 supplies the inventory, lanes, fixtures, durable groups, and immutable planning primitives. It does **not** self-certify the milestone. TEST-G2 remains the next standalone roadmap item and must independently prove the lane/selection milestone using these contracts.
