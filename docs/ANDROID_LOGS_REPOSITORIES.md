# Android Structured Logs and Repositories

This document describes the Android Phase 0–6 implementation shipped in Android frontend 1.13.0.

## Scope

The Android frontend now contains two connected surfaces:

- **Logs** indexes Android execution events and authoritative Devtool log files, then presents searchable run records with Timeline, Problems, Raw, and Metadata views.
- **Repos** ports the desktop repository registry and full Git workflow to Android. It discovers Termux and chroot repositories, correlates them with runs, and exposes repository activity, bounded diffs, commit history, branches, stashes, guarded local mutations, and safe remote synchronization.


## Persistence and migration

`DevtoolDatabase` is the single SQLite store for:

- runs
- structured log entries and FTS4 search
- log sources and incremental file cursors
- repositories
- repository activity events
- durable repository operation records and synthetic Git runs
- run-to-repository, artifact, commit, transaction-backup, and authoritative-log metadata

The previous `SharedPreferences` run array is migrated once. Repository refreshes use update-or-insert semantics rather than SQLite `REPLACE`, preserving correlated events and runs. Selecting a repository subdirectory rekeys it transactionally to the canonical Git root.

## Root log indexing

`RootLogIndexer` uses the existing root command broker and scans these sources:

- Termux XDG Devtool state and logs
- the Termux Tasker/HUD event stream
- supported `chrootctl` chroots
- Android/root bridge log locations

Each file cursor stores source ID, path, inode, byte offset, size, modification time, parser type, and a partial trailing line. A refresh:

1. lists bounded Devtool-specific files as the correct environment user;
2. detects rotation or truncation using inode and size;
3. reads only appended bytes in bounded chunks;
4. preserves incomplete UTF-8 lines for the next pass;
5. parses JSONL events or normalizes text output;
6. updates run metadata and inserts searchable structured entries;
7. never modifies the source log.

The parser treats summaries such as `0 errors` and `0 failed` as non-errors, extracts common diagnostic codes, classifies phases and tools, and records source path metadata. JSONL sequence IDs prevent repeated event insertion when bridge snapshots are rewritten.

## Root execution boundary

Root is a broker, not the default Git identity.

- Termux inspection runs under the Termux UID and Termux home/PATH.
- Chroot inspection runs through `chrootctl` as the repository owner when known.
- Root-owned paths remain explicitly marked as Root.
- Output is bounded and every root command has a timeout.
- Repository reads and mutations execute through the same owner-aware broker. Network commands disable interactive credential prompts, use bounded timeouts, never force push, and only permit fast-forward pulls. Branch names and stash references are validated before command construction.

Repository identity is based on environment, chroot, Linux user, and canonical guest path. Identical visible paths in Termux and a chroot therefore remain distinct.

## Logs UI

The Logs destination provides:

- automatic stale-source refresh and manual **Refresh root logs**
- full-text search across run metadata and structured entries
- status, project type, and severity filters
- compact run cards with health totals and duration
- exact-run notification and `devtool://logs/run/<id>` deep links
- Timeline, Problems, Raw, and Metadata views
- authoritative source path and source ID display
- direct navigation from a run to its correlated repository

The Raw view merges Android-captured output with indexed authoritative lines and applies a display bound without deleting stored records.

## Repositories UI

The fifth navigation destination, **Repos**, provides:

- saved repository registry
- temporary `LIVE · NOT SAVED` repositories from active runs
- root-aware Termux, chroot, and root directory selection
- bounded repository discovery with build/cache directory pruning
- clean, modified, missing, not-Git, and error states
- branch, upstream, remote, ahead/behind, project type, environment, and active-run state
- search and saved/modified/clean/live/missing/Termux/chroot filters

Repository details contain:

- **Overview**: identity, path, status, branch, remote, project, and actions
- **Changes**: NUL-safe porcelain-v2 groups, bounded diffs, file/bulk stage and unstage, staged commit creation, stash including untracked files, and strongly confirmed tracked-only hard reset
- **Commits**: recent history, commit details, remote links, and Devtool run/artifact correlation
- **Branches**: local/remote branches, validated creation and checkout, tracking-branch creation, and selectable stash pop
- **Sync**: active and queued Git operations, cancellation, local incoming/outgoing previews, passive SSH/credential diagnostics, and recent operation logs
- **Activity**: repository-correlated Devtool and Git-operation events
- **Runs**: active and historical runs with exact log navigation
- **Metadata**: canonical IDs and environment information

Navigation actions include Use in Run, refresh, owner-aware shell launch, root browsing, path copy, save, registry removal, and direct repository-filtered Logs navigation. Phase 4 provides fetch with prune/tags, fast-forward-only pull, non-force push with automatic first-upstream setup, staged commits, branch operations, stash/pop, and typed-confirmation tracked-only hard reset. Phase 5 records each Git action as a synthetic structured run and durable repository operation, then links it across Repos, Logs, commits, artifacts, and notifications.

## Phase 5 cross-feature integration

Git mutations now enter a durable FIFO queue per repository. Different repositories may execute concurrently, while one repository never runs overlapping mutations. Queue entries are stored before execution and include a synthetic `git` run ID. Their queued, running, successful, failed, cancelled, interrupted, and orphaned states are searchable through the normal Logs surface.

Cancellation uses a `DEVTOOL_ANDROID_OPERATION_ID` marker inherited by the root, Termux, chroot, shell, and Git process tree. The broker polls cancellation while commands run and terminates matching marked processes through root. At app startup, persisted queued/running operations are checked against `/proc/*/environ`: dead operations become interrupted; surviving detached processes become explicit orphaned operations that the user may terminate from the Sync tab.

The Sync tab also presents local upstream divergence using `rev-list --left-right --count` and bounded commit ranges. This is based on existing refs and never fetches automatically. Remote diagnostics inspect the remote transport, Git and SSH availability, agent/askpass state, credential-helper configuration, readable identity presence, and known-host state. The diagnostic is passive and never invokes `ssh -T`, `git ls-remote`, or another network probe.

Logs filters persist search, status, project, severity, and repository selection. Repository cards expose active Git-operation health; operation notifications reopen the exact repository Sync tab; operation-run metadata opens the repository; and repository detail can open all correlated logs.

## Phase 6 mobile hardening and polish

Responsive behavior is centralized in `AdaptiveLayout`. Phones keep bottom navigation and horizontally scrolling detail tabs. At 720 dp and wider, the app moves primary navigation into a side rail. At 840 dp and wider, repository and log details use a navigation rail beside persistent content. Extra-large text disables crowded two-pane layouts and stacks action groups vertically. Content padding, navigation widths, and action orientation come from the same policy so foldables and tablets do not become stretched phone layouts.

The Sync tab now displays elapsed time for running work and queue position plus wait time for pending work. Queued operations can move earlier or later only inside their repository's FIFO queue. Reordering updates the in-memory queue while holding its repository lock, persists `queue_order`, and cannot cross repositories or move the active operation. Cancellation and orphan termination remain available.

Runs may be pinned from Logs cards or the exact-run viewer. Automatic retention never removes pinned or active runs. Settings expose completed-run age, maximum retained completed runs, completed Git-operation age, and repository-activity age. Cleanup affects only Android-indexed SQLite data, never authoritative logs, repositories, artifacts, Git history, or untracked files. Manual cleanup can optionally checkpoint and compact the database; failures are reported without blocking the UI callback.

Remote diagnostics remain passive. When local prerequisites are missing, the app offers reviewable, copyable setup commands for Git, OpenSSH, known-host setup, SSH keys/agents, or HTTPS credential helpers. It never executes those suggestions automatically, performs no `ssh -T` or `git ls-remote` probe, displays no credential material, and warns that host fingerprints must be verified independently.

Instrumentation covers phone, tablet/two-pane, and enlarged-text profiles, plus labeled buttons and 48 dp touch targets. Source contracts cover schema migration, retention exclusions, queue isolation, passive diagnostics, and release metadata.

## Phase 3 working tree and commit history

The Android repository detail surface now includes horizontally scrollable **Changes** and **Commits** destinations alongside Overview, Activity, Runs, and Metadata.

Working-tree reads use `git status --porcelain=v2 -z --branch --untracked-files=all`. The parser consumes NUL records rather than splitting lines or taking the final whitespace token, so paths containing spaces and rename source paths remain intact. Records are presented as Conflicted, Renamed, Deleted, Staged, Modified, or Untracked.

File actions are deliberately bounded:

- Stage a file or all changes.
- Unstage a file or all staged changes.
- Inspect working, staged, and untracked diffs.
- Discard only tracked working-tree changes after an exact-path confirmation.
- Preserve the staged version when discarding a separate working-tree edit.
- Never delete untracked files.

Every path is validated as repository-relative and passed after Git's `--` separator. Operations run through the root broker as the actual Termux/chroot owner and are serialized per repository. Their result is recorded in repository activity.

Commit history loads the latest 100 commits with full and abbreviated hashes, author, ISO timestamp, refs, and HEAD state. Commit details show metadata, file status/stat output, remote links where available, and indexed Devtool runs/artifacts correlated by repository and commit hash.

Phase 4 completes those deferred workflows with guarded staged commits, validated branch operations, bounded remote synchronization, stash management, and strongly confirmed tracked-only reset.

## Phase 7 support bundles and field diagnostics

The Android Settings diagnostics area now includes a local support bundle generator. The bundle is a redacted JSON snapshot intended for troubleshooting root indexing, repository discovery, Git-operation queues, retention and run/log correlation without requiring users to paste raw logs.

Support bundles include app/version metadata, Android SDK information, root bridge state, retention and backend settings, SQLite counts, current busy states, an active-run summary, bounded recent runs, bounded repository summaries and bounded operation summaries. Raw stdout, stderr and launch-log bodies are intentionally excluded.

Recent command strings are opt-in through **Include redacted recent commands** and are disabled by default. Even when enabled, exported text is scrubbed for the active bridge token, HTTPS credentials, Authorization header values, common token/password/secret/key assignments and private-key blocks. The app only copies or shares the bundle after an explicit user action.
