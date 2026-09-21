from rich.console import Console
from devtool.test_counts import TestCountAccumulator as CountAccumulator
from devtool.generic_progress import GenericProgressTracker
from devtool.npm_progress import NpmProgressTracker


def test_cargo_summary_normalizes_counts():
    p = CountAccumulator.for_argv(["cargo", "test"])
    assert p.feed("test result: FAILED. 10 passed; 2 failed; 3 ignored; 0 measured; 0 filtered out")
    assert (p.counts.passed, p.counts.failed, p.counts.skipped, p.counts.total) == (10, 2, 3, 15)
    p.feed("test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out")
    assert (p.counts.passed, p.counts.failed, p.counts.skipped, p.counts.total) == (12, 2, 3, 17)


def test_maven_summary_includes_errors_as_failed():
    p = CountAccumulator.for_argv(["mvn", "test"])
    assert p.feed("Tests run: 12, Failures: 2, Errors: 1, Skipped: 3")
    assert (p.counts.passed, p.counts.failed, p.counts.skipped, p.counts.total) == (6, 3, 3, 12)


def test_go_json_is_identity_deduped():
    p = CountAccumulator.for_argv(["go", "test", "-json", "./..."])
    line='{"Action":"pass","Package":"demo/a","Test":"TestOne"}'
    assert p.feed(line); assert p.feed(line)
    p.feed('{"Action":"skip","Package":"demo/a","Test":"TestTwo"}')
    assert (p.counts.passed, p.counts.failed, p.counts.skipped, p.counts.total) == (1, 0, 1, 2)


def test_jest_and_vitest_summaries_normalize_counts():
    p = CountAccumulator(framework="node")
    assert p.feed("Tests:       1 failed, 2 skipped, 10 passed, 13 total")
    assert (p.counts.passed, p.counts.failed, p.counts.skipped, p.counts.total) == (10, 1, 2, 13)
    assert p.feed("Tests  2 failed | 20 passed | 3 skipped (25)")
    assert (p.counts.passed, p.counts.failed, p.counts.skipped, p.counts.total) == (20, 2, 3, 25)


def test_node_tap_counts_are_normalized():
    p = CountAccumulator(framework="node")
    for line in ("# tests 8", "# pass 6", "# fail 1", "# skip 1"):
        assert p.feed(line)
    assert (p.counts.passed, p.counts.failed, p.counts.skipped, p.counts.total) == (6, 1, 1, 8)


def test_generic_tracker_publishes_cargo_counts():
    t = GenericProgressTracker(["cargo", "test"], ".", "test", Console(force_terminal=False))
    t.update_callback=lambda _: None
    t.update_from_line("test result: ok. 7 passed; 0 failed; 2 ignored; 0 measured; 0 filtered out")
    s=t.snapshot()
    assert (s["tests_passed"], s["tests_failed"], s["tests_skipped"], s["tests_total"]) == (7,0,2,9)


def test_npm_tracker_publishes_jest_counts():
    t = NpmProgressTracker(["npm", "test"], ".", "test", Console(force_terminal=False))
    t.update_callback=lambda _: None
    t.update_from_line("Tests: 2 failed, 1 skipped, 9 passed, 12 total")
    s=t.snapshot()
    assert (s["tests_passed"], s["tests_failed"], s["tests_skipped"], s["tests_total"]) == (9,2,1,12)


def test_maven_multimodule_uses_results_aggregates_without_class_double_count():
    p = CountAccumulator.for_argv(["mvn", "test"])
    # Per-class summaries are useful fallback data but must not be added once
    # Surefire emits the module aggregate after Results:.
    p.feed("[INFO] Tests run: 2, Failures: 0, Errors: 0, Skipped: 0")
    p.feed("[INFO] Results:")
    p.feed("[INFO] Tests run: 5, Failures: 1, Errors: 0, Skipped: 1")
    p.feed("[INFO] Tests run: 3, Failures: 0, Errors: 0, Skipped: 0")
    p.feed("[INFO] Results:")
    p.feed("[INFO] Tests run: 4, Failures: 0, Errors: 1, Skipped: 0")
    assert (p.counts.passed, p.counts.failed, p.counts.skipped, p.counts.total) == (6, 2, 1, 9)


def test_common_argv_detection_variants():
    assert CountAccumulator.for_argv(["./mvnw", "test"]).framework == "maven"
    assert CountAccumulator.for_argv(["cargo", "test", "--workspace"]).framework == "cargo"
    assert CountAccumulator.for_argv(["go", "test", "-json", "./..."]).framework == "go"
    assert CountAccumulator.for_argv(["pnpm", "test"]).framework == "node"
    assert CountAccumulator.for_argv(["yarn", "test"]).framework == "node"


def test_npm_progress_state_carries_normalized_test_counts():
    t = NpmProgressTracker(["npm", "test"], ".", "test", Console(force_terminal=False))
    t.update_from_line("Tests: 2 failed, 1 skipped, 9 passed, 12 total")
    state = t._progress_state()
    assert (state.tests_passed, state.tests_failed, state.tests_skipped, state.tests_total) == (9, 2, 1, 12)


def test_node_tap_todo_cancelled_and_skipped_are_not_lost():
    p = CountAccumulator(framework="node")
    for line in ("# tests 10", "# pass 5", "# fail 1", "# skipped 1", "# todo 2", "# cancelled 1"):
        assert p.feed(line)
    assert (p.counts.passed, p.counts.failed, p.counts.skipped, p.counts.total) == (5, 2, 3, 10)


def test_generic_progress_state_carries_cargo_counts():
    t = GenericProgressTracker(["cargo", "test"], ".", "test", Console(force_terminal=False))
    t.update_from_line("test result: ok. 7 passed; 1 failed; 2 ignored; 0 measured; 0 filtered out")
    state = t._progress_state()
    assert (state.tests_passed, state.tests_failed, state.tests_skipped, state.tests_total) == (7, 1, 2, 10)
