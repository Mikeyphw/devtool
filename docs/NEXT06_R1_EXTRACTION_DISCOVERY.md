# NEXT-06-R1: read-only dependency discovery for extraction

Implementation baseline: current reviewed Git `main` commit (check overlay source pin).

## CLI / API

```bash
# draft.json is an existing v1 extraction manifest with both complete pinned Git
# identities, candidate move prefixes and forbidden-reference tokens.
devtool migration discover /absolute/path/draft.json
python -m devtool.repository_extraction discover /absolute/path/draft.json
```

The **discover** operation is read-only and needs no `--yes`. It reads the
pinned Git blobs from each repository, inspects ignored/generated inventory
under selected move prefixes, and returns a deterministic JSON proposal.
It never creates/updates an extraction journal, runs Git commit, touches files,
executes a GitHub POST, or gives a new manifest implicit approval. It has no
root/device/Gradle dependencies.

## Evidence semantics

- `candidate_moves` are *the user's unapproved draft move hints*. They have
  not been validated by prepare/execute. Project marker evidence is included.
- `additional_move_suggestions` are unselected roots inferred conservatively
  from tracked project markers. Destination path is `null`, not guessed.
- `references` include source/destination file, 1-based line, matching token,
  SHA-256 of the entire line, origin move prefix, source kind, signal,
  conservative confidence and classification. **Raw line text is never emitted**
  because CI/configuration lines may contain tokens or credentials.
- `rewrite_suggestions` have `replacement: null`; they are *not* valid v1
  `source_rewrites` or `destination_rewrites` entries. Every rewrite needs an
  explicit human-selected exact string and destination before a reviewed
  manifest can be used.
- `generated_backups` report ignored path/size, no ignored file contents.
- `unscanned` describes files intentionally skipped (symlinks, binary,
  oversized, non-UTF8, long lines). `scan_complete=false` means discovery
  must not be mistaken for exhaustive dependency evidence.
- `must_rewrite` is a high-priority candidate, **not approval**;
  `likely_reference_review` is ambiguous; `external_consumer_unknown` marks
  references in the destination; `generated_backup` marks ignored output.
  False-positive substrings are excluded from references and a bounded sample
  appears in `irrelevant_matches` with classification `irrelevant`.
- All data is pinned to source/destination head and tree. Moving HEAD, dirty
  worktrees, submodules, nested repositories, and unsafe ignored symlinks
  fail closed rather than silently proceeding.

## Limits and intentional non-claims

This is a bounded text/Git heuristic rather than a semantic language-server,
compiler, or graph of every consumer. At most 5,000 tracked files combined,
32 MiB of aggregate scan budget, 256 KiB per text file, 500 emitted
references, 1,000 ignored files and 32 additional project-root candidates.
Some indirect dependencies (dynamic imports, custom generators, external
repositories and CI references using variables) will remain unknown. Files
outside the selected prefix are scanned for textual references, but discovery
cannot prove that no external consumer exists. Human review is mandatory.

The existing v1 `migration plan/prepare/execute/resume` engine remains the
sole mutation authority. Discovery does **not** weaken its source/branch/tree
pinning, exact rewrite cardinality, forbidden-source-reference scans,
generated-data escrow, fail-closed partial destination commit recovery, or
independent two-repository commit semantics. A successful discovery is
**REVIEW_REQUIRED**, not an extraction pass or release qualification.

## Acceptance scope

Run the targeted tests for new discovery + legacy extraction/partial-commit
recovery + checked-in CLI surfaces, then the separate manual 24-shard GitHub
Actions regression against the new commit. No hosted/consumer/device/release
qualification can be inferred from this source-level work.
