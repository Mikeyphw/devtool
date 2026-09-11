from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import time
import unittest

from devtool.cli import _single_run_reservation, build_parser, main
from devtool.config import generate_template, load_project_config
from devtool.workflow_runtime import build_workspace_specs, plan_workspace_targets
from devtool.summary import build_json_summary
from devtool.workflow_scheduler import (
    build_execution_plan_from_specs,
    run_workflow_specs,
    scheduling_conflict,
)


class Repo:
    def __init__(self, root: Path):
        self.root = root

    def config(self, text: str, *, target: str = "", ambiguous: bool = False):
        (self.root / ".devtool.toml").write_text(text, encoding="utf-8")
        return load_project_config(str(self.root), None, target_name=target, allow_ambiguous_target=ambiguous)


class SchedulerPolicyTests(unittest.TestCase):
    def test_workspace_scheduler_parses_and_serializes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = Repo(root).config(
                '''
[project]
runner = "command"
[workspace.scheduler]
parallel = true
max_parallel_nodes = 4
max_gradle_nodes = 1
max_dotnet_nodes = 2
[targets.api]
runner = "command"
'''
            )
            self.assertTrue(cfg.workspace.scheduler.parallel)
            self.assertEqual(cfg.workspace.scheduler.max_parallel_nodes, 4)
            self.assertEqual(cfg.workspace.scheduler.max_dotnet_nodes, 2)

    def test_generated_config_documents_workspace_scheduler(self):
        with tempfile.TemporaryDirectory() as td:
            text = generate_template(Path(td))
            self.assertIn("[workspace.scheduler]", text)
            self.assertIn("max_parallel_nodes", text)
            self.assertIn("max_gradle_nodes", text)

    def test_invalid_scheduler_value_fails_config_load(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaisesRegex(SystemExit, "max_parallel_nodes"):
                Repo(root).config(
                    '''
[project]
runner = "command"
[workspace.scheduler]
max_parallel_nodes = -1
[targets.api]
runner = "command"
'''
                )


class ScopeAndWaveTests(unittest.TestCase):
    def _parallel_cfg(self, root: Path):
        (root / "a").mkdir()
        (root / "b").mkdir()
        (root / "join").mkdir()
        return Repo(root).config(
            '''
[project]
runner = "command"
[workspace]
default_target = "app"
[workspace.scheduler]
parallel = true
max_parallel_nodes = 4
[jobs.a]
runner = "command"
root = "a"
command = ["python3", "-c", "print('a')"]
inputs = ["input.txt"]
outputs = ["out/**"]
[jobs.b]
runner = "command"
root = "b"
command = ["python3", "-c", "print('b')"]
inputs = ["input.txt"]
outputs = ["out/**"]
[jobs.join]
runner = "command"
root = "join"
command = ["python3", "-c", "print('join')"]
inputs = ["../a/out/**", "../b/out/**"]
outputs = ["out/**"]
[targets.app]
runner = "command"
root = "."
[targets.app.workflows]
build = [
  { id = "a", ref = "job:a", depends_on = [] },
  { id = "b", ref = "job:b", depends_on = [] },
  { id = "join", ref = "job:join", depends_on = ["a", "b"] },
]
'''
        )

    def test_independent_known_write_nodes_share_wave(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = self._parallel_cfg(Path(td))
            specs = build_workspace_specs(cfg, ["app"], "build")
            plan = build_execution_plan_from_specs(cfg, specs, parallel_requested=True, max_parallel_nodes=4)
            self.assertIsNotNone(plan)
            assert plan is not None
            self.assertEqual(len(plan.waves), 2)
            self.assertEqual(len(plan.waves[0].nodes), 2)
            self.assertEqual(len(plan.waves[1].nodes), 1)
            self.assertTrue(plan.parallel_effective)
            self.assertTrue(plan.reservation_paths_known)
            self.assertEqual(set(plan.reservation_paths), {"a/out", "b/out", "join/out"})

    def test_unknown_write_node_serializes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a").mkdir(); (root / "b").mkdir()
            cfg = Repo(root).config(
                '''
[project]
runner = "command"
[workspace]
default_target = "app"
[jobs.a]
runner = "command"
root = "a"
command = ["true"]
[jobs.b]
runner = "command"
root = "b"
command = ["true"]
outputs = ["out/**"]
[targets.app]
runner = "command"
[targets.app.workflows]
build = [
 { id="a", ref="job:a", depends_on=[] },
 { id="b", ref="job:b", depends_on=[] },
]
'''
            )
            plan = build_execution_plan_from_specs(cfg, build_workspace_specs(cfg, ["app"], "build"), parallel_requested=True)
            assert plan is not None
            self.assertFalse(plan.parallel_effective)
            self.assertFalse(plan.reservation_paths_known)
            self.assertEqual([len(w.nodes) for w in plan.waves], [1, 1])

    def test_write_read_overlap_serializes_even_with_distinct_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shared").mkdir(); (root / "b").mkdir()
            cfg = Repo(root).config(
                '''
[project]
runner = "command"
[workspace]
default_target = "app"
[jobs.writer]
runner = "command"
root = "."
command = ["true"]
inputs = ["source/**"]
outputs = ["shared/**"]
[jobs.reader]
runner = "command"
root = "."
command = ["true"]
inputs = ["shared/**"]
outputs = ["b/**"]
[targets.app]
runner = "command"
[targets.app.workflows]
build = [
 { id="writer", ref="job:writer", depends_on=[] },
 { id="reader", ref="job:reader", depends_on=[] },
]
'''
            )
            plan = build_execution_plan_from_specs(cfg, build_workspace_specs(cfg, ["app"], "build"), parallel_requested=True)
            assert plan is not None
            self.assertEqual([len(w.nodes) for w in plan.waves], [1, 1])
            self.assertIn("write/read overlap", scheduling_conflict(plan.nodes[0], plan.nodes[1]))

    def test_gradle_resource_cap_serializes_gradle_nodes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a").mkdir(); (root / "b").mkdir()
            # Jobs use explicit argv so no real Gradle project is needed; the
            # runner identity is enough to exercise resource classification.
            cfg = Repo(root).config(
                '''
[project]
runner = "command"
[workspace]
default_target = "app"
[workspace.scheduler]
parallel = true
max_gradle_nodes = 1
[jobs.a]
runner = "gradle"
root = "a"
command = ["true"]
inputs = ["in"]
outputs = ["out"]
[jobs.b]
runner = "gradle"
root = "b"
command = ["true"]
inputs = ["in"]
outputs = ["out"]
[targets.app]
runner = "command"
[targets.app.workflows]
build = [
 { id="a", ref="job:a", depends_on=[] },
 { id="b", ref="job:b", depends_on=[] },
]
'''
            )
            plan = build_execution_plan_from_specs(cfg, build_workspace_specs(cfg, ["app"], "build"), parallel_requested=True)
            assert plan is not None
            self.assertEqual([len(w.nodes) for w in plan.waves], [1, 1])


class ParallelExecutionTests(unittest.TestCase):
    def test_safe_independent_nodes_really_execute_concurrently(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a").mkdir(); (root / "b").mkdir()
            cfg = Repo(root).config(
                '''
[project]
runner = "command"
[workspace]
default_target = "app"
[jobs.a]
runner = "command"
root = "a"
command = ["sh", "-c", "sleep 0.45; echo a"]
inputs = ["in"]
outputs = ["out"]
[jobs.b]
runner = "command"
root = "b"
command = ["sh", "-c", "sleep 0.45; echo b"]
inputs = ["in"]
outputs = ["out"]
[targets.app]
runner = "command"
[targets.app.workflows]
build = [
 { id="a", ref="job:a", depends_on=[] },
 { id="b", ref="job:b", depends_on=[] },
]
'''
            )
            specs = build_workspace_specs(cfg, ["app"], "build")
            started = time.perf_counter()
            results, raw, plan = run_workflow_specs(
                cfg, specs, verbose=False, dry_run=False, continue_on_error=False,
                log_path=None, parallel_requested=True, max_parallel_nodes=2,
            )
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 0.82, f"expected concurrent execution, got {elapsed:.3f}s")
            self.assertTrue(all(item.returncode == 0 for item in results))
            self.assertTrue(plan and plan.parallel_effective)
            self.assertIn("a", raw); self.assertIn("b", raw)

    def test_failed_parallel_root_blocks_dependent_join(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ("a", "b", "join"):
                (root / name).mkdir()
            cfg = Repo(root).config(
                '''
[project]
runner = "command"
[workspace]
default_target = "app"
[jobs.a]
runner = "command"
root = "a"
command = ["python3", "-c", "raise SystemExit(3)"]
inputs = ["in"]
outputs = ["out"]
[jobs.b]
runner = "command"
root = "b"
command = ["true"]
inputs = ["in"]
outputs = ["out"]
[jobs.join]
runner = "command"
root = "join"
command = ["python3", "-c", "print('SHOULD_NOT_RUN')"]
inputs = ["../a/out", "../b/out"]
outputs = ["out"]
[targets.app]
runner = "command"
[targets.app.workflows]
build = [
 { id="a", ref="job:a", depends_on=[] },
 { id="b", ref="job:b", depends_on=[] },
 { id="join", ref="job:join", depends_on=["a","b"] },
]
'''
            )
            results, raw, _ = run_workflow_specs(
                cfg, build_workspace_specs(cfg, ["app"], "build"), verbose=False,
                dry_run=False, continue_on_error=True, log_path=None,
                parallel_requested=True, max_parallel_nodes=2,
            )
            join = next(item for item in results if item.metadata.get("workflow_node_id") == "join")
            self.assertTrue(join.skipped)
            self.assertEqual(join.metadata.get("workflow_node_state"), "blocked")
            self.assertNotIn("SHOULD_NOT_RUN", raw)

    def test_fail_fast_skips_later_independent_wave(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ("a", "later"):
                (root / name).mkdir()
            cfg = Repo(root).config(
                '''
[project]
runner = "command"
[workspace]
default_target = "app"
[jobs.a]
runner = "command"
root = "a"
command = ["false"]
inputs = ["in"]
outputs = ["out"]
[jobs.later]
runner = "command"
root = "later"
command = ["python3", "-c", "print('later')"]
# unknown write set deliberately forces this independent root to a later wave
[targets.app]
runner = "command"
[targets.app.workflows]
build = [
 { id="a", ref="job:a", depends_on=[] },
 { id="later", ref="job:later", depends_on=[] },
]
'''
            )
            results, raw, _ = run_workflow_specs(
                cfg, build_workspace_specs(cfg, ["app"], "build"), verbose=False,
                dry_run=False, continue_on_error=False, log_path=None,
                parallel_requested=True, max_parallel_nodes=2,
            )
            later = next(item for item in results if item.metadata.get("workflow_node_id") == "later")
            self.assertTrue(later.skipped)
            self.assertEqual(later.metadata.get("workflow_node_state"), "skipped")
            self.assertNotIn("\nlater\n", raw)


class ReservationAndPlanCliTests(unittest.TestCase):
    def test_single_run_reservation_uses_known_workflow_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api").mkdir()
            cfg = Repo(root).config(
                '''
[project]
runner = "command"
[workspace]
default_target = "api"
[targets.api]
runner = "command"
root = "api"
inputs = ["src/**"]
outputs = ["dist/**"]
build_command = "true"
'''
            )
            args = build_parser().parse_args(["--repo", str(root), "build"])
            target, paths, known, _ = _single_run_reservation(cfg, args)
            self.assertEqual(target, "api")
            self.assertTrue(known)
            self.assertEqual(paths, ("api/dist",))

    def test_unknown_output_keeps_exclusive_reservation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api").mkdir()
            cfg = Repo(root).config(
                '''
[project]
runner = "command"
[workspace]
default_target = "api"
[targets.api]
runner = "command"
root = "api"
build_command = "true"
'''
            )
            args = build_parser().parse_args(["--repo", str(root), "build"])
            _target, paths, known, _ = _single_run_reservation(cfg, args)
            self.assertFalse(known)
            self.assertEqual(paths, ())

    def test_plan_json_contains_graph_schedule_scopes_commands(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api").mkdir()
            Repo(root).config(
                '''
[project]
runner = "command"
[workspace]
default_target = "api"
[targets.api]
runner = "command"
root = "api"
inputs = ["src/**"]
outputs = ["dist/**"]
build_command = "printf build"
'''
            )
            stream = io.StringIO()
            with redirect_stdout(stream):
                code = main(["--repo", str(root), "plan", "build", "--json", "--workflow-parallel"])
            envelope = json.loads(stream.getvalue())
            self.assertEqual(code, 0)
            payload = envelope["data"] if "data" in envelope else envelope
            self.assertEqual(payload["action"], "build")
            self.assertEqual(payload["targets"], ["api"])
            self.assertTrue(payload["schedule"]["parallel_requested"])
            self.assertEqual(payload["schedule"]["nodes"][0]["writes"], ["api/dist"])
            self.assertEqual(payload["commands"][0]["node"], payload["graph"]["nodes"][0]["key"])

    def test_plan_dot_emits_dependency_edges(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ("a", "b"):
                (root / name).mkdir()
            Repo(root).config(
                '''
[project]
runner = "command"
[workspace]
default_target = "app"
[jobs.a]
runner = "command"
root = "a"
command = ["true"]
outputs = ["out"]
[jobs.b]
runner = "command"
root = "b"
command = ["true"]
outputs = ["out"]
[targets.app]
runner = "command"
[targets.app.workflows]
build = ["job:a", "job:b"]
'''
            )
            stream = io.StringIO()
            with redirect_stdout(stream):
                code = main(["--repo", str(root), "plan", "build", "--dot"])
            text = stream.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("digraph devtool_workflow", text)
            self.assertIn(" -> ", text)


    def test_workflows_json_exposes_expanded_schedule(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api").mkdir()
            Repo(root).config(
                """
[project]
runner = "command"
[workspace]
default_target = "api"
[targets.api]
runner = "command"
root = "api"
outputs = ["dist/**"]
[targets.api.workflows]
build = ["runner:command#build"]
"""
            )
            # The command runner action intentionally has no configured build
            # command, so use a reusable job for a concrete plan instead.
            (root / ".devtool.toml").write_text(
                """
[project]
runner = "command"
[workspace]
default_target = "api"
[jobs.build-api]
runner = "command"
root = "api"
command = ["true"]
outputs = ["dist/**"]
[targets.api]
runner = "command"
root = "api"
[targets.api.workflows]
build = ["job:build-api"]
""", encoding="utf-8")
            stream = io.StringIO()
            with redirect_stdout(stream):
                code = main(["--repo", str(root), "workflows", "--json"])
            payload = json.loads(stream.getvalue())
            self.assertEqual(code, 0)
            data = payload["data"] if "data" in payload else payload
            schedule = data["workflows"]["api"]["build"]["schedule"]
            self.assertEqual(schedule["schema_version"], 1)
            self.assertEqual(schedule["nodes"][0]["writes"], ["api/dist"])

    def test_json_summary_has_top_level_workflow_observability(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api").mkdir()
            cfg = Repo(root).config(
                """
[project]
runner = "command"
[workspace]
default_target = "api"
[jobs.build-api]
runner = "command"
root = "api"
command = ["true"]
outputs = ["dist/**"]
[targets.api]
runner = "command"
root = "api"
[targets.api.workflows]
build = ["job:build-api"]
"""
            )
            specs = build_workspace_specs(cfg, ["api"], "build")
            results, _raw, plan = run_workflow_specs(
                cfg, specs, verbose=False, dry_run=False, continue_on_error=False,
                log_path=None, parallel_requested=True, max_parallel_nodes=2,
            )
            assert plan is not None
            for result in results:
                result.metadata["workflow_schedule"] = {
                    "parallel_requested": plan.parallel_requested,
                    "parallel_effective": plan.parallel_effective,
                    "wave_count": len(plan.waves),
                }
            summary = build_json_summary(results, repo_root=str(root))
            self.assertEqual(summary["workflow"]["targets"], ["api"])
            self.assertEqual(summary["workflow"]["nodes"][0]["state"], "passed")
            self.assertTrue(summary["workflow"]["schedule"]["parallel_requested"])

    def test_workflow_parallel_cli_flags_parse_for_build_and_apply(self):
        parser = build_parser()
        build = parser.parse_args(["build", "--workflow-parallel", "--workflow-max-workers", "3"])
        self.assertTrue(build.workflow_parallel)
        self.assertEqual(build.workflow_max_workers, 3)
        validate = parser.parse_args(["validate", "--parallel"])
        self.assertTrue(validate.validation_parallel)


if __name__ == "__main__":
    unittest.main()
