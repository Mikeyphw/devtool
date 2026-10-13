"""Guard the live G4 Android AAB unit-test JSON-runtime recovery."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "android/devtool-live-hud/app/build.gradle"
JAVA = ROOT / ("android/devtool-live-hud/app/src/test/java/"
               "io/github/mikeceead/devtoolhud/ExecutionGraphStateTest.java")
WORKFLOW = ROOT / ".github/workflows/devtool-android-aab-candidate.yml"


def test_real_json_runtime_is_explicitly_test_only_and_version_pinned():
    source = APP.read_text()
    assert "testImplementation 'org.json:json:20240303'" in source
    assert source.count("org.json:json:20240303") == 1
    assert "implementation 'org.json:json:20240303'" not in source.replace("testImplementation 'org.json:json:20240303'", "")
    assert "unitTests.returnDefaultValues = true" in source


def test_live_failed_graph_tests_are_preserved_and_json_parser_is_verified():
    source = JAVA.read_text()
    for name in (
        "localJvmUsesRealJsonParserNotMockableAndroidStubs",
        "parsesCanonicalAccountingWithoutCompletingFailedWork",
        "exposesTypedChildOperationsWithStableIds",
        "preservesBlockedAsDistinctTerminalStatus",
        "malformedGraphFailsClosedToUnavailable",
    ):
        assert name in source
    assert 'new JSONObject("{' in source
    assert 'json.getString("state")' in source
    assert 'json.getInt("count")' in source


def test_unsigned_aab_still_requires_real_unit_tests_and_release_lint():
    source = APP.read_text()
    assert "dependsOn 'bundleRelease', 'testDebugUnitTest', 'lintRelease'" in source
    assert "if (!unsignedBundleCandidate || releaseSigningReady)" in source
    assert "if (unsignedBundleRequested && !unsignedBundleCandidate)" in source
    workflow = WORKFLOW.read_text()
    assert ":app:devtoolBundleUnsignedReleaseCandidate" in workflow
    assert "if-no-files-found: error" in workflow
    assert "tools/actions_g4_aab_manifest.py emit" in workflow


def test_no_app_runtime_json_dependency_injected():
    source = APP.read_text()
    deps = source.split("\ndependencies {", 1)[-1]
    lines = [line.strip() for line in deps.splitlines()]
    assert "implementation 'org.json:json:20240303'" not in lines
    assert "api 'org.json:json:20240303'" not in lines
    assert "debugImplementation 'org.json:json:20240303'" not in lines
    assert "releaseImplementation 'org.json:json:20240303'" not in lines
