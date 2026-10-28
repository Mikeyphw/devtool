from __future__ import annotations

import re
from pathlib import Path


DESTRUCTIVE_PATTERN = re.compile(
    r"shutil\.rmtree\(|\.unlink\(|os\.remove\(|os\.rmdir\(|rm -rf"
)

# R03B destructive fixed-point classification.  A raw hit is allowed only when
# the owning file is explicitly classified here.  The classification is not a
# blanket safety waiver: it records why the remaining destructive primitive is
# not an unowned cleanup/GC authority for this campaign fixed point.
CLASSIFIED_FILES: dict[str, str] = {
    # Actions and async retention appeared after the original R03B inventory.
    # Each callsite below is an operation-owned atomic/temp cleanup, not broad
    # repository/artifact-history collection or caller-selected deletion.
    "devtool/actions_android.py": "Verified Android package extraction removes only its exclusive mkstemp staging file after atomic publish; the SHA and destination no-clobber checks remain enforced.",
    "devtool/actions_artifacts.py": "GitHub artifact acquisition removes only its exclusive temporary ZIP after hash verification and atomic no-overwrite publication.",
    "devtool/actions_dispatch.py": "Durable dispatch journal writes remove only an exclusive, local mkstemp file after atomic journal replacement.",
    "devtool/actions_runs.py": "GitHub log download cleanup removes only the exclusive mkstemp file in the Actions remote-log storage root.",
    "devtool/actions_unsigned.py": "Unsigned APK publication deletes only an exclusively created destination on failed stream/hash verification; never removes an existing destination.",
    "devtool/log_retention_worker.py": "Asynchronous log-retention worker removes its own private atomic request temp and consumed request marker after locking and identity check; rotation uses canonical logstore policy.",
    "devtool/android/_standalone.py": "Android toolchain/provider bootstrap cache: global SDK/NDK/Gradle cache installation, digest-checked temp staging, lock cleanup, and explicit provider prune policy; not repository evidence cleanup.",
    "devtool/android/evidence.py": "Android evidence temp staging/write-finalization; no repository GC authority.",
    "devtool/android/execution_snapshot.py": "Android snapshot temp file finalization cleanup; no source/evidence pruning authority.",
    "devtool/android/signing.py": "Android signing temporary artifact cleanup; private signing state is not pruned by name.",
    "devtool/android_integration.py": "Android integration generated temp output cleanup after explicit operation scope.",
    "devtool/ai_prompt.py": "Prompt export temp/legacy file cleanup; not provider/run evidence cleanup.",
    "devtool/artifact_pipeline.py": "Pipeline temp artifact staging cleanup under artifact transaction authority.",
    "devtool/artifact_registry.py": "Registry atomic temp file cleanup; registry rows/tombstones remain canonical authority.",
    "devtool/artifact_cleanup_worker.py": "Explicit post-apply consumed source-artifact cleanup worker: external file/symlink unlink is restricted to the apply-source cleanup path and produces LIFE receipts; repository-internal sources use canonical LIFE ownership. This classification is inventory coverage only, not caller authorization or proof of arbitrary-path safety.",
    "devtool/artifact_trust.py": "Artifact trust temp file/cache cleanup, not destructive repository mutation.",
    "devtool/artifacts.py": "Artifact construction/finalization temp cleanup and legacy metadata retirement; artifact payload mutation remains plan-owned.",
    "devtool/bootstrap.py": "Bootstrap generated report/temp cleanup; not RUN/RER/provider evidence cleanup.",
    "devtool/cache_cleanup.py": "Cache cleanup planner/applier surface; its paths are cache-scoped and separately policy-selected.",
    "devtool/cleanup_lifecycle.py": "Canonical LIFE cleanup executor; this is the authority being converged on, not a bypass.",
    "devtool/core/execution_subscribers.py": "Subscriber socket/temp unlink cleanup; no persistent execution evidence pruning.",
    "devtool/desktop/gtk_control_center.py": "Desktop UI temp/socket cleanup outside repository cleanup authority.",
    "devtool/desktop/preferences.py": "Desktop preferences atomic temp cleanup.",
    "devtool/desktop/repository_store.py": "Desktop repository-store temp cleanup; not source/evidence GC.",
    "devtool/distributed_transport.py": "Distributed transport temp cursor/artifact staging cleanup after explicit transport operation.",
    "devtool/federation_artifacts.py": "Federation artifact local temp/staging cleanup; drift replacement now fails closed elsewhere.",
    "devtool/federation_git_transport.py": "Git federation bundle/temp/private-ref cleanup under explicit git transport authority.",
    "devtool/federation_roadmap_fixtures.py": "Test/fixture materialization cleanup; no production evidence authority.",
    "devtool/git_bisect.py": "Git bisect temp/workspace cleanup under git diagnostic command authority.",
    "devtool/git_dirty_state.py": "Dirty-tree backup/restore mechanics: deletes are path-scoped restoration of previously snapshotted user state, not name-based cleanup.",
    "devtool/git_rerere.py": "Rerere temp/private evidence cleanup under git conflict-evidence authority.",
    "devtool/gradle_takeover_g1_gate.py": "Gate scratch artifact cleanup; shell assets are now lowered through LIFE elsewhere.",
    "devtool/hooks/isolation.py": "Hook isolation restores exact pre-hook path snapshots and removes only paths introduced by that hook isolation scope.",
    "devtool/interactive/fzf.py": "Interactive selection temp file cleanup.",
    "devtool/invocation_origin.py": "Invocation-origin temp marker cleanup.",
    "devtool/ipc.py": "IPC socket unlink cleanup; not source/evidence cleanup.",
    "devtool/logstore.py": "Logstore atomic temp cleanup; retention is handled by LIFE/log retention surfaces.",
    "devtool/oci_execution.py": "OCI execution temp definition/state cleanup; cache drift replacement now fails closed in provider staging.",
    "devtool/oci_workspace.py": "OCI workspace temp lease/workdir cleanup under workspace ownership.",
    "devtool/overlay/equivalence.py": "Overlay equivalence temp extraction cleanup.",
    "devtool/overlay/merge.py": "Overlay merge temp extraction cleanup.",
    "devtool/release_qualification.py": "Release qualification temp report cleanup.",
    "devtool/repository_pack.py": "Repository-pack temp/staged file cleanup; pack registry GC drift is protected by R03B.",
    "devtool/repository_pack_registry.py": "Repository-pack registry atomic temp cleanup.",
    "devtool/runners.py": "Runner temp directory cleanup plus canonical ResolvedArtifactPlan/apply mutation execution; not ad hoc retention/GC cleanup.",
    "devtool/single_run.py": "Single-run transient state cleanup.",
    "devtool/ssh_lifecycle.py": "SSH lifecycle private state cleanup guarded by run/session lifecycle.",
    "devtool/ssh_profiles.py": "SSH profile temp config cleanup.",
    "devtool/ssh_provider_evidence.py": "SSH provider evidence temp file cleanup.",
    "devtool/ssh_qualification.py": "SSH qualification temp report cleanup.",
    "devtool/ssh_workspace.py": "SSH workspace temp/symlink marker cleanup; leases no longer use recursive shell delete.",
    "devtool/tasker.py": "Tasker live-state cleanup is now lowered through LIFE; remaining unlink is atomic/temp state handling.",
    "devtool/termux_chroot_runner.py": "Termux chroot secret staging temp cleanup plus registered provider-resource release path.",
    "devtool/transaction_worktree.py": "Git transaction worktree integration/removal under transaction journal authority, not cleanup GC.",
    "devtool/transactions.py": "Private transaction journal/index/backup cleanup after completion/rollback; active/corrupt authorities fail closed through LIFE before unrelated cleanup.",
    "devtool/validation_reports.py": "Validation report atomic temp cleanup.",
    "devtool/validation_sessions.py": "Validation session temp marker cleanup and explicit rollback-point release.",
    "devtool/wrapper_launcher.py": "Wrapper launch temp/generated shim cleanup under wrapper lifecycle authority.",
}

# FC09/G1 review of NEW production deletion callsites. Unlike the historical
# file-level classifications above, each newly observed primitive is pinned by
# its exact source expression and occurrence count. Any added, changed, or
# removed destructive call requires fresh review, without freezing unrelated
# lines or resurrecting historical source SHA assertions.
NEW_REVIEWED_DESTRUCTIVE_CALLS: dict[str, tuple[str, ...]] = {
 'devtool/go_output_spool.py': ('os.unlink(filename)', 'self._path.unlink(missing_ok=True)'),'devtool/actions_adoption.py': ('os.unlink(temp)', 'file.unlink()'),
 'devtool/actions_graph_export.py': ('item.unlink(missing_ok=True)',),
 'devtool/actions_language_providers.py': ('path.unlink(missing_ok=True)',),
 'devtool/actions_pipeline.py': ('handoff.unlink(missing_ok=True)',),
 'devtool/actions_private_handoff.py': ('destination.unlink()',),
 'devtool/actions_rm12_jvm.py': ("output.unlink(missing_ok=True);raise JvmError('RM-12 JAR changed during output "
                                 "materialization')",),
 'devtool/actions_ux01_acceptance.py': ('if os.path.exists(name):os.unlink(name)',),
 'devtool/actions_ux02_acceptance.py': ('if os.path.exists(tmp):os.unlink(tmp)',),
 'devtool/adoption_batch.py': ('Path(path).unlink(missing_ok=True)',),
 'devtool/gradle_options_repair.py': ('Path(temporary).unlink(missing_ok=True)',),
 'devtool/guarded_git.py': ('Path(idx).unlink()', 'Path(idx).unlink(missing_ok=True)', 'lock.unlink()'),
 'devtool/output_scopes.py': ('os.rmdir(name, dir_fd=fd)',
                              'os.unlink(name, dir_fd=fd)',
                              'os.unlink(name, dir_fd=parent_fd)',
                              'os.rmdir(root.name, dir_fd=parent_fd)',
                              'os.unlink(name, dir_fd=parent_fd)',
                              'os.rmdir(root.name, dir_fd=parent_fd)',
                              'os.rmdir(path.name, dir_fd=parent_fd)',
                              'os.unlink(journal, dir_fd=run_fd)'),
 'devtool/repository_extraction.py': ('if os.path.exists(tmp):os.unlink(tmp)',
                                      'if temp.exists():shutil.rmtree(temp)',
                                      'if target.exists():target.unlink()',
                                      'if path.exists():path.unlink()'),
 'devtool/rmp_c03_acceptance.py': ('try: os.unlink(name)',),
 'devtool/ssh_r07_reliability.py': ('tmp.unlink()',),
 'devtool/workflow_retry.py': ("'ksud', 'fastboot', 'adb install', 'rm -rf', 'git reset', 'git push')",
                               'if os.path.exists(name): os.unlink(name)')}

NEW_REVIEWED_RATIONALES: dict[str, str] = {
 'devtool/go_output_spool.py': 'Go test stdout transport owns a private mkstemp file (0600), removes the exclusive pathname if initial setup fails, and unlinks only its own spool after the producer has stopped and both file descriptors close. This does not authorize repository, run, log or consumer-evidence cleanup.','devtool/actions_adoption.py': 'Exclusive atomic staging unlink plus rollback only for workflow bytes just created '
                                'and verified against the attempted payload; not cleanup by name.',
 'devtool/actions_graph_export.py': 'Rollback of verified graph handoff files copied in the current operation after a '
                                    'no-overwrite precheck; no broad evidence collection.',
 'devtool/actions_language_providers.py': 'RM-06 generated-workflow atomic-export rollback after '
                                          'source-commit/selection and destination-absence verification.',
 'devtool/actions_pipeline.py': 'One-shot private capability handoff removal after invocation, including failed child '
                                'launch; only own generated secret.',
 'devtool/actions_private_handoff.py': 'A08 user-requested signed APK/AAB export uses O_CREAT|O_EXCL|O_NOFOLLOW; '
                                       'exception cleanup is destination-specific, not repository GC. Potential '
                                       'concurrent pathname replacement warrants separate inode-guard hardening '
                                       'review.',
 'devtool/actions_rm12_jvm.py': 'RM-12 non-overwriting JAR output only deleted after post-copy hash mismatch; not '
                                'historical signing evidence cleanup.',
 'devtool/actions_ux01_acceptance.py': 'Acceptance JSON receipt private tempfile removed after atomic report '
                                       'placement.',
 'devtool/actions_ux02_acceptance.py': 'Acceptance JSON receipt private tempfile removed after atomic report '
                                       'placement.',
 'devtool/adoption_batch.py': 'Adoption batch report tempfile cleanup after atomic fsync/replace; no directory-wide '
                              'artifact pruning.',
 'devtool/gradle_options_repair.py': 'Gradle options repair uses private mkstemp and cleans only unpromoted temporary '
                                     'file.',
 'devtool/guarded_git.py': 'FC02 isolated temporary Git index cleanup plus private exclusive swap-lock cleanup; '
                           'repository tree is guarded by explicit source pins.',
 'devtool/output_scopes.py': 'Owner-journal-locked output-scope temp subtree deletion with no-follow dirfd and inode '
                             'rechecks; refuses stale, swapped and malformed ownership.',
 'devtool/repository_extraction.py': 'Optional explicitly source-pinned two-checkout extraction, private candidate '
                                     'temp cleanup, and scoped source transfer/escrow cleanup; RM19 split remains '
                                     'CANCELLED and is not activated by this inventory.',
 'devtool/rmp_c03_acceptance.py': 'C03 acceptance report exclusive tempfile cleanup after no-clobber JSON receipt '
                                  'publication.',
 'devtool/ssh_r07_reliability.py': 'SSH reliability report private tempfile cleanup only, distinct from remote session '
                                   'evidence retention.',
 'devtool/workflow_retry.py': 'Risk phrase rm -rf is denial vocabulary, not executed shell deletion; other hit is '
                              'bounded atomic workflow-start record tempfile cleanup.'}

HIGH_RISK_CLOSED_FILES = {
    "devtool/ssh_tmux_runner.py",
    "devtool/ssh_resilience.py",
    "devtool/federation_sync.py",
}


def _destructive_hits() -> dict[str, list[tuple[int, str]]]:
    root = Path(__file__).resolve().parents[1]
    hits: dict[str, list[tuple[int, str]]] = {}
    for path in sorted((root / "devtool").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if DESTRUCTIVE_PATTERN.search(line):
                hits.setdefault(rel, []).append((line_no, line.strip()))
    return hits


def test_r03b_destructive_fixed_point_has_no_unclassified_production_hits() -> None:
    hits = _destructive_hits()
    unclassified = sorted(set(hits) - set(CLASSIFIED_FILES) - set(NEW_REVIEWED_DESTRUCTIVE_CALLS))
    assert not unclassified, "unclassified destructive hits: " + ", ".join(unclassified)


def test_r03b_closed_high_risk_remote_cleanup_paths_have_no_raw_destructive_hits() -> None:
    hits = _destructive_hits()
    offenders = {name: rows for name, rows in hits.items() if name in HIGH_RISK_CLOSED_FILES}
    assert offenders == {}


def test_r03b_apply_source_worker_is_explicitly_classified_without_safety_waiver() -> None:
    key = "devtool/artifact_cleanup_worker.py"
    hits = _destructive_hits()
    assert key in hits
    assert any(".unlink(" in line for _, line in hits[key])
    rationale = CLASSIFIED_FILES[key]
    assert "external" in rationale and "LIFE" in rationale
    assert "not caller authorization" in rationale


def test_g1_new_destructive_calls_have_reviewed_exact_current_scope() -> None:
    from collections import Counter

    assert set(NEW_REVIEWED_RATIONALES) == set(NEW_REVIEWED_DESTRUCTIVE_CALLS)
    hits = _destructive_hits()
    for path, reviewed_lines in NEW_REVIEWED_DESTRUCTIVE_CALLS.items():
        # A future callsite added in the same module cannot silently inherit
        # this inventory classification; a reviewer must explain its authority.
        assert Counter(line for _, line in hits.get(path, [])) == Counter(reviewed_lines), path
        assert len(NEW_REVIEWED_RATIONALES[path]) >= 40
