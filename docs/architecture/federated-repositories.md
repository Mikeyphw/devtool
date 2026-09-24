# Federated repositories

## FRX-01: repository identity contract

FRX-01 establishes repository identity without enabling federation or child-repository discovery.

A loaded `ProjectConfig` now owns a canonical repository identity containing:

- canonical repository root;
- stable identity fingerprint for that repository instance;
- display name;
- repository config path;
- Git top-level and Git common-dir identity when available;
- a digest of the Git origin URL when available (the URL itself is never exposed).

The identity is serialized into config-resolution output, workflow planning metadata, executable workflow `CommandSpec` metadata, and EXO run snapshots. This gives later federation work one identity primitive instead of relying on filesystem strings at each surface.

### Config ownership

Repository identity distinguishes the requested project root from the configuration-owning repository root. Existing Devtool behavior where a parent/workspace `.devtool.toml` governs a deeper project remains valid and resolves to the parent's repository identity.

A config owned by a descendant or unrelated directory is rejected. This prevents reverse/cross-repository config borrowing while preserving established workspace behavior.

### What FRX-01 does not do

FRX-01 does not add `[federation]`, discover child repositories, create cross-repository DAG edges, or execute child repositories. Those begin in later FRX overlays.

## FRX-02: explicit federation configuration

Federation enrollment is repository-owned and opt-in. Merely placing another
Git repository below a Devtool repository does not enroll it.

```toml
[federation]
schema = 1

[federation.repositories.android-app]
path = "../android-app"

[federation.repositories.optional-tool]
path = "tools/optional-tool"
required = false

[federation.groups.product]
repositories = ["android-app", "optional-tool"]
```

Relative paths are resolved from the **configuration-owning repository root**,
not from the process cwd or a deeper requested project root. Required children
must exist as directories at config-load time; optional children may be absent
and remain visible as unavailable enrollment records. Self-enrollment, duplicate
canonical roots (including symlink aliases), unknown fields, unsupported schema
versions, and invalid group references fail closed.

FRX-02 deliberately does not inspect child `.devtool.toml` files or execute
child work. Repository discovery/ownership resolution begins in FRX-03.
