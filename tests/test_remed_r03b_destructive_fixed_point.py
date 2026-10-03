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
    "devtool/android/_standalone.py": "Android toolchain/provider bootstrap cache: global SDK/NDK/Gradle cache installation, digest-checked temp staging, lock cleanup, and explicit provider prune policy; not repository evidence cleanup.",
    "devtool/android/evidence.py": "Android evidence temp staging/write-finalization; no repository GC authority.",
    "devtool/android/execution_snapshot.py": "Android snapshot temp file finalization cleanup; no source/evidence pruning authority.",
    "devtool/android/signing.py": "Android signing temporary artifact cleanup; private signing state is not pruned by name.",
    "devtool/android_integration.py": "Android integration generated temp output cleanup after explicit operation scope.",
    "devtool/ai_prompt.py": "Prompt export temp/legacy file cleanup; not provider/run evidence cleanup.",
    "devtool/artifact_pipeline.py": "Pipeline temp artifact staging cleanup under artifact transaction authority.",
    "devtool/artifact_registry.py": "Registry atomic temp file cleanup; registry rows/tombstones remain canonical authority.",
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
    unclassified = sorted(set(hits) - set(CLASSIFIED_FILES))
    assert not unclassified, "unclassified destructive hits: " + ", ".join(unclassified)


def test_r03b_closed_high_risk_remote_cleanup_paths_have_no_raw_destructive_hits() -> None:
    hits = _destructive_hits()
    offenders = {name: rows for name, rows in hits.items() if name in HIGH_RISK_CLOSED_FILES}
    assert offenders == {}
