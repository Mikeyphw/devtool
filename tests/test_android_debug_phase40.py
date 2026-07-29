from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_phase40_path_resolution_model_exports_safe_repair_actions() -> None:
    model = read("RepositoryPathResolution.java")
    for symbol in (
        "RepositoryPathResolution",
        "RepairAction",
        "KIND_RECLASSIFY_TERMUX",
        "KIND_RECLASSIFY_CHROOT",
        "KIND_RECLASSIFY_ROOT",
        "KIND_UPDATE_PATH",
        "KIND_USE_DISCOVERED_GIT_ROOT",
        "safe_repairs",
        "detected_path_family",
        "expected_path_family",
        "plainText()",
    ):
        assert symbol in model
    assert "It does not delete" not in model


def test_phase40_path_resolver_classifies_namespace_mismatches_and_git_root_repairs() -> None:
    resolver = read("RepositoryPathResolver.java")
    for symbol in (
        "repo_termux_path_saved_as_chroot",
        "repo_termux_path_saved_as_root",
        "repo_chroot_path_saved_as_termux_or_root",
        "repo_root_path_saved_as_termux",
        "repo_invalid_home_expansion",
        "repo_discovered_git_root_available",
        "repo_missing_path_repair_available",
        "termuxHostToChrootGuess",
        "expandChrootHome",
        "addReclassifyTermux",
        "addReclassifyChroot",
        "addReclassifyRoot",
        "addUseDiscoveredGitRoot",
        "supportSummary(RepositoryRecord repository)",
        "findAction(RepositoryRecord repository, String actionId)",
    ):
        assert symbol in resolver
    assert "git reset" not in resolver
    assert "git clean" not in resolver
    assert "rm -rf" not in resolver


def test_phase40_store_applies_metadata_repair_without_git_or_destructive_actions() -> None:
    store = read("RepositoryStore.java")
    for symbol in (
        "applyPathRepair(Context context, String id",
        "RepositoryPathResolution.RepairAction",
        "database.rekeyRepository(oldId, repaired)",
        "REPO_PATH_REPAIR_APPLIED",
        "Repository metadata repaired",
        "repaired.status = \"checking\"",
        "repaired.saved = true",
        "remapGitRoot",
    ):
        assert symbol in store
    forbidden = ("git reset", "git clean", "rm -rf", "deleteRepository(id)")
    repair_body = store[store.index("applyPathRepair"):store.index("static void remove")]
    for text in forbidden:
        assert text not in repair_body


def test_phase40_repository_detail_surfaces_repair_card_and_confirmation() -> None:
    detail = read("RepositoryDetailActivity.java")
    for symbol in (
        "repositoryPathResolutionCard(repository)",
        "Repository path resolver",
        "Copy resolution",
        "confirmPathRepair",
        "Apply repair",
        "This changes only Android repository metadata",
        "It does not delete files, modify Git history, reset the worktree or run Git",
        "RepositoryStore.applyPathRepair",
    ):
        assert symbol in detail
    assert detail.count("repositoryPathResolutionCard(repository)") >= 2


def test_phase40_support_bundle_and_classifier_include_path_resolution() -> None:
    support = read("SupportBundle.java")
    classifier = read("DebugIssueClassifier.java")
    assert "path_resolution" in support
    assert "RepositoryPathResolver.supportSummary(repository)" in support
    for key in (
        "repo_path_repair_applied",
        "repo_termux_path_saved_as_chroot",
        "repo_termux_path_saved_as_root",
        "repo_chroot_path_saved_as_termux_or_root",
        "repo_discovered_git_root_available",
        "repo_invalid_home_expansion",
        "repo_missing_path_repair_available",
    ):
        assert key in classifier
