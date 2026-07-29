from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "android/devtool-live-hud/app/src/main"
JAVA = APP / "java/io/github/mikeceead/devtoolhud"
MANIFEST = APP / "AndroidManifest.xml"
DATA_RULES = APP / "res/xml/data_extraction_rules.xml"

INTENTIONAL_PATH_CLASSES = (
    "RepositoryLocation.java",
    "RepositoryPathResolver.java",
    "RootCommandBroker.java",
    "RootExecutionPlan.java",
    "RootLogIndexer.java",
    "TermuxCommandRunner.java",
    "TermuxPaths.java",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_intentional_termux_root_paths_have_local_sdcardpath_suppression() -> None:
    for name in INTENTIONAL_PATH_CLASSES:
        text = read(JAVA / name)
        assert "import android.annotation.SuppressLint;" in text
        assert '@SuppressLint("SdCardPath")\nfinal class ' in text

    assert 'TERMUX_HOME = "/data/data/com.termux/files/home"' in read(JAVA / "RepositoryLocation.java")
    assert 'TERMUX_PREFIX = "/data/data/com.termux/files/usr"' in read(JAVA / "RootCommandBroker.java")
    assert 'HOME = "/data/data/com.termux/files/home"' in read(JAVA / "TermuxPaths.java")


def test_manifest_declares_data_extraction_rules_while_backup_remains_disabled() -> None:
    manifest = read(MANIFEST)
    assert 'android:allowBackup="false"' in manifest
    assert 'android:dataExtractionRules="@xml/data_extraction_rules"' in manifest
    assert DATA_RULES.is_file()


def test_data_extraction_rules_exclude_debug_evidence_from_cloud_and_transfer() -> None:
    rules = read(DATA_RULES)
    assert "Devtool HUD stores run traces, repository paths, bridge metadata" in rules
    for section in ("cloud-backup", "device-transfer"):
        start = rules.index(f"<{section}>")
        end = rules.index(f"</{section}>", start)
        block = rules[start:end]
        for domain in ("file", "database", "sharedpref", "external"):
            assert f'<exclude domain="{domain}" path="." />' in block
