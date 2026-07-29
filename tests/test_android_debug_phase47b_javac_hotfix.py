from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "android/devtool-live-hud/app/src/main/java/io/github/mikeceead/devtoolhud"


def text(name: str) -> str:
    return (SRC / name).read_text()


def test_repository_path_resolver_exposes_resolve_alias_for_existing_call_sites():
    source = text("RepositoryPathResolver.java")
    assert "static RepositoryPathResolution resolve(RepositoryRecord repository)" in source
    assert "return analyze(repository);" in source


def test_repository_store_records_path_repair_event_with_timestamp_argument():
    source = text("RepositoryStore.java")
    assert "database.addRepositoryEvent(repaired.repositoryId, \"\", \"REPO_PATH_REPAIR_APPLIED\"," in source
    assert "System.currentTimeMillis());" in source
    broken = "database.addRepositoryEvent(repaired.repositoryId, \"\", \"REPO_PATH_REPAIR_APPLIED\",\n                action.label + \" · \" + location.environment + \" · \" + location.path);"
    assert broken not in source


def test_repository_doctor_plain_text_keeps_executor_plan_newline_escaped():
    source = text("RepositoryDoctorResult.java")
    assert '+ (directRootFallback ? " (direct root fallback)" : "") + "\\n"' in source
    assert '+ (directRootFallback ? " (direct root fallback)" : "") + "\n"\n"' not in source
