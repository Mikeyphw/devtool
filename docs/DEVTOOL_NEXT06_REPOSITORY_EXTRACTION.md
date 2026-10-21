# NEXT-06 — Source-locked two-repository extraction

**Scope:** source implementation and disposable Git fixture validation, not real `mike-cli-suite` → `xbsctl` acceptance. Implemented in `devtool/repository_extraction.py`. This is **not** a globally atomic two-repository Git transaction and does not qualify any release.

## CLI

```sh
devtool --no-hud migration plan /absolute/extraction.json --json
devtool --no-hud migration prepare /absolute/extraction.json --state-dir /absolute/external/state --yes --json
devtool --no-hud migration execute /absolute/extraction.json --state-dir /absolute/external/state --yes --json
devtool --no-hud migration status /absolute/extraction.json --state-dir /absolute/external/state --json
devtool --no-hud migration resume /absolute/extraction.json --state-dir /absolute/external/state --yes --json
```

`plan` and `status` read Git and prior journal evidence without writing to either repository, without allocating a journal, and without deleting files. `prepare` writes SHA-256-checked ignored/generated backups and optionally a Git source-history bundle **outside both checkouts**, before any tracked data is changed. `execute` commits the destination first and source second, with strict checked identity and a durable journal after each commit. `resume` reconciles an interrupted output stage or exact unrecorded commit, never rebases, resets, amends, pushes, or claims a cross-repository rollback. Interrupted arbitrary edits block.

## Manifest (example only — replace every source identity)

```json
{
  "schema": "devtool.repo-extraction.v1",
  "source": {
    "path": "/absolute/Code/mike-cli-suite",
    "head": "<full authenticated source Git SHA>",
    "tree": "<full source Git tree SHA>",
    "branch": "main",
    "remote": "origin",
    "remote_url": "https://example.org/owner/mike-cli-suite.git"
  },
  "destination": {
    "path": "/absolute/Code/xbsctl",
    "head": "<full authenticated destination Git SHA>",
    "tree": "<full destination Git tree SHA>",
    "branch": "main",
    "remote": "origin",
    "remote_url": "https://example.org/owner/xbsctl.git"
  },
  "moves": [{"from": "apps/xbs", "to": "."}],
  "source_rewrites": [
    {"path": "some-existing-tracked-config", "old": "<exact old reference>", "new": "<exact reviewed replacement>"}
  ],
  "destination_rewrites": [
    {"path": "go.mod", "old": "<exact inherited relative module reference>", "new": "<exact standalone replacement>"}
  ],
  "generated_paths": ["android-app/local.properties"],
  "forbid_source_references": ["apps/xbs", "<old target or wrapper identifier>"],
  "reference_allowlist": [],
  "preserve_history": true,
  "clean_ignored": false
}
```

**These are placeholders, not an executable migration recipe.** `generated_paths` is only for additional ignored regular files outside moved prefixes; remove it if not needed. The ordinary scan automatically covers ignored outputs *within* every moved directory, including generated build caches and `local.properties`. `source_rewrites` and `destination_rewrites` are reviewed **exactly once** byte substitutions and are not arbitrary scripts. The `forbid_source_references` inventory must include relevant target IDs, wrapper aliases, target paths, hosted CI references, installer registry entries and release references to prevent known dangling ownership links; each remaining small tracked text file is scanned and must not retain a forbidden value unless explicitly allowlisted. Large unscanned text blocks are refused without an explicit path exemption. There is no guess-based automatic deletion of workflow, release, or wrapper code.

## Preflight contracts and evidence

* Source and destination are disjoint normal Git roots, with exact HEAD, tree, branch, and configured remote URL locks. **Local remote configuration equality is not independent remote authentication.** Unknown untracked files, symlinks, special files, submodules, dirty Git state, unresolved Devtool overlay transactions, overlapping prefixes, existing destination paths, and unsafe path traversal block extraction.
* `git ls-files` determines Git-owned source files; `git ls-files --others --ignored --exclude-standard` identifies ignored/generated files. Every ignored/generated source file in extraction scope must have a durable hash-checked external backup. Unknown *nonignored* files block. No blanket deletion of ignored directories is permitted.
* The external journal records manifest SHA, exact source/destination initial commits, candidate output hashes, expected modes, generated backup path/hash/size, optional source-history bundle digest, and each independently committed revision. Git 100755 executable modes are retained. Bundling Git refs preserves a **separate source-history artifact**; it does not graft that history into the new destination repository or prove remote origin.
* Go `go.mod` and `go.work` content is checked for inherited `../..` references and requires explicit reviewed mapping before commit. Modern Devtool targets using `runner="command"` are not rewritten into deprecated `type="command"` assumptions. The migration never invokes a project release build, signs outputs, dispatches GitHub workflows, or silently marks consumer acceptance as passing.
* Destination commit must be complete and checked before any source owned file is removed. A later source failure leaves the destination commit durably recorded; the operation is partial until explicit recovery. `clean_ignored=true` opts into unlinking **only the exact previously escrowed ignored files** after both Git commits; the default retains all ignored files untouched in the original checkout. No Git HEAD, index, or committed history is automatically rolled back.

## Qualification

Disposable tests cover complete two-repo extraction and replay, partial destination staging, interrupted Nth repository, tampered/missing backup, local properties, ignored build-stage cache, unknown source, dirty source, symlinks, source references, executable mode, and Go relative-module repair. These local tests do **not** establish real `mike-cli-suite`/`xbsctl` Git locks, native Termux acceptance, live hosted CI results, transferred native-target validity, GitHub remote identity, or release qualification. Full XBS migration should use newly provided independent source archives/heads and the real consumer test matrix before closing DT-037–DT-040.
