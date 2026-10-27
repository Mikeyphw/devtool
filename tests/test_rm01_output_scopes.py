from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from devtool.config import load_project_config
from devtool.output_scopes import (
    OutputScopeError, parse_output_scopes, reservation_scope,
    provision_node_temp, cleanup_owned_temp,
)
from devtool.workflow_runtime import build_workspace_specs
from devtool.workflow_scheduler import build_execution_plan_from_specs, run_workflow_specs, scheduling_conflict


HEADER = '''
[project]
runner = "command"
[workspace]
default_target = "app"
[workspace.scheduler]
parallel = true
max_parallel_nodes = 2
'''
FOOTER = '''
[targets.app]
runner = "command"
[targets.app.workflows]
build = [
    {id = "one", ref = "job:one", depends_on = []},
    {id = "two", ref = "job:two", depends_on = []},
]
'''


def config_for(root: Path, first: str, second: str, *, extras: str = ''):
    (root / '.devtool.toml').write_text(HEADER + first + second + extras + FOOTER, encoding='utf8')
    return load_project_config(str(root), str(root / '.devtool.toml'), target_name='app')


class RM01TypedOutputTests(unittest.TestCase):
    def test_reject_path_traversal_and_unsafe_schemas(self):
        for kind, path in [('run-temp', '../escape'), ('shared-cache', '/tmp/root'),
                           ('repository', '../../outside'), ('repository', '.git/hooks'),
                           ('run-temp', './'), ('run-temp', 'a\\b')]:
            with self.subTest(kind=kind, path=path):
                with self.assertRaises(OutputScopeError):
                    parse_output_scopes([{'kind': kind, 'path': path}], context='test')
        with self.assertRaisesRegex(OutputScopeError, 'reservation'):
            parse_output_scopes([{'kind': 'external', 'path': '/tmp/report.json'}], context='test')
        with self.assertRaisesRegex(OutputScopeError, 'persistent'):
            parse_output_scopes([{'kind': 'repository', 'path': 'output', 'cleanup': 'on-terminal'}], context='test')
        with self.assertRaisesRegex(OutputScopeError, 'lifetime=run'):
            parse_output_scopes([{'kind': 'run-temp', 'path': 'out', 'cleanup': 'none'}], context='test')
        with self.assertRaisesRegex(OutputScopeError, 'lifetime=run'):
            parse_output_scopes([{'kind': 'run-temp', 'path': 'out', 'lifetime': 'node'}], context='test')
        with self.assertRaisesRegex(OutputScopeError, 'hash_retention'):
            parse_output_scopes([{'kind': 'repository', 'path': 'output', 'hash_retention': 'always'}], context='test')
        with self.assertRaisesRegex(OutputScopeError, 'duplicate'):
            parse_output_scopes([{'kind': 'run-temp', 'path': 'out'}]*2, context='test')
        with self.assertRaisesRegex(OutputScopeError, 'unknown keys'):
            parse_output_scopes([{'kind': 'run-temp', 'path': 'out', 'uid': 0}], context='test')

    def test_disjoint_owned_temp_scopes_are_parallel_eligible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cfg = config_for(root, '''
[jobs.one]
runner = "command"
command = ["true"]
output_scopes = [{kind="run-temp", path="receipts/out.json", hash_retention="always"}]
''', '''
[jobs.two]
runner = "command"
command = ["true"]
output_scopes = [{kind="run-temp", path="receipts/out.json"}]
''')
            plan = build_execution_plan_from_specs(cfg, build_workspace_specs(cfg, ['app'], 'build'))
            self.assertTrue(plan.parallel_effective)
            self.assertEqual(len(plan.waves[0].nodes), 2)
            self.assertNotEqual(plan.nodes[0].writes, plan.nodes[1].writes)
            self.assertTrue(all(node.writes_known for node in plan.nodes))
            self.assertTrue(plan.nodes[0].output_scopes)
            self.assertIn('run-temp', str(plan.as_dict()))

    def test_shared_cache_and_external_conflicts_are_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = '''
[jobs.one]
runner = "command"
command = ["true"]
output_scopes = [{kind="shared-cache", path="pip/wheels"}]
'''
            second = '''
[jobs.two]
runner = "command"
command = ["true"]
output_scopes = [{kind="shared-cache", path="pip/wheels/subdir"}]
'''
            cfg = config_for(root, first, second)
            plan = build_execution_plan_from_specs(cfg, build_workspace_specs(cfg, ['app'], 'build'))
            self.assertFalse(plan.parallel_effective)
            self.assertIn('write/write overlap', scheduling_conflict(*plan.nodes))
            first = first.replace('kind="shared-cache", path="pip/wheels"', 'kind="external", path="/tmp/xbs-shared", reservation="device"')
            second = second.replace('kind="shared-cache", path="pip/wheels/subdir"', 'kind="external", path="/tmp/xbs-unrelated", reservation="device"')
            cfg = config_for(root, first, second)
            plan = build_execution_plan_from_specs(cfg, build_workspace_specs(cfg, ['app'], 'build'))
            self.assertIn('global reservation conflict', scheduling_conflict(*plan.nodes))

    def test_typed_repo_output_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            (root / 'escape').symlink_to(Path(outside), target_is_directory=True)
            specs = parse_output_scopes([{'kind':'repository', 'path':'escape/artifacts'}], context='test')
            with self.assertRaisesRegex(OutputScopeError, 'symlink'):
                reservation_scope(specs[0], node_key='n1', repo_root=root, job_root=root)

    def test_read_only_cannot_hide_declared_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = '''
[jobs.one]
runner = "command"
command = ["true"]
output_scopes = [{kind="run-temp", path="out"}]
[jobs.one.metadata]
read_only = true
'''
            second = '''
[jobs.two]
runner = "command"
command = ["true"]
'''
            with self.assertRaisesRegex(SystemExit, 'read_only'):
                config_for(root, first, second)

    def test_owned_temp_created_for_command_and_removed_after_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / 'state'
            first = '''
[jobs.one]
runner = "command"
command = ["python3", "-c", "import os,pathlib; d=pathlib.Path(os.environ['DEVTOOL_WORKFLOW_TMPDIR']); (d/'test.txt').write_text('value'); print(d)"]
output_scopes = [{kind="run-temp", path="test.txt", hash_retention="always"}]
'''
            second = '''
[jobs.two]
runner = "command"
command = ["true"]
outputs = ["two-output"]
'''
            cfg = config_for(root, first, second)
            specs = build_workspace_specs(cfg, ['app'], 'build')
            with patch.dict(os.environ, {'XDG_STATE_HOME': str(state)}):
                results, _, _ = run_workflow_specs(cfg, specs, verbose=False, dry_run=False,
                                                    continue_on_error=False, log_path=None)
            self.assertTrue(all(int(item.returncode or 0) == 0 for item in results), results)
            import hashlib
            recorded = [record for item in results for record in item.metadata.get('workflow_temporary_output_hashes', [])]
            self.assertTrue(any(record.get('sha256') == hashlib.sha256(b'value').hexdigest() for record in recorded), recorded)
            roots = list((state / 'devtool' / 'workflow-outputs').rglob('test.txt'))
            self.assertFalse(roots, f'owned output was not cleaned: {roots}')

    def test_ownership_guard_rejects_replaced_temp_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(os.environ, {'XDG_STATE_HOME': str(root/'state')}):
                owned, identity = provision_node_temp(root, 'rm01-run', 'node')
                backup = owned.with_name(owned.name + '.foreign')
                owned.rename(backup)
                owned.mkdir()
                (owned/'sentinel').write_text('foreign')
                self.assertFalse(cleanup_owned_temp(owned, identity))
                self.assertTrue((owned/'sentinel').exists())
                (owned/'sentinel').unlink()
                owned.rmdir()
                self.assertTrue(cleanup_owned_temp(backup, identity))


if __name__ == '__main__':
    unittest.main()
