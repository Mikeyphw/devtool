"""RM15 HF01's independent no-Gradle, source-scoped adversarial regression tests."""
from __future__ import annotations
from pathlib import Path
import ast
import runpy
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

class RM15HF01(unittest.TestCase):
    def test_setup_dotnet_pin_has_exact_v5_ref_and_denies_lookalikes(self):
        from devtool.roadmap_gate_audit import _PINNED_NODE24, _ACTIONS, audit_workflows
        exact='26b0ec14cb23fa6904739307f278c14f94c95bf1'
        self.assertEqual(_ACTIONS['actions/setup-dotnet'],'v5')
        self.assertEqual(_PINNED_NODE24['actions/setup-dotnet'],exact)
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)
            workflows=source/'.github'/'workflows'
            workflows.mkdir(parents=True)
            for n in range(20):
                (workflows/f'gate-{n:02}.yml').write_text('name: gate\nsteps:\n  - uses: actions/checkout@v6\n')
            selected=workflows/'gate-00.yml'
            selected.write_text('steps:\n  - uses: actions/setup-dotnet@'+exact+'\n')
            self.assertEqual(audit_workflows(source)['status'],'PASS')
            selected.write_text('steps:\n  - uses: actions/setup-dotnet@'+exact[:-1]+'0\n')
            receipt=audit_workflows(source)
            self.assertEqual(receipt['status'],'FAIL')
            self.assertTrue(any('setup-dotnet@' in reason for reason in receipt['first_party_violations']))

    def test_exact_reviewed_destructive_callsite_inventory(self):
        rows=runpy.run_path(str(ROOT/'tests/test_remed_r03b_destructive_fixed_point.py'))
        _destructive_hits=rows['_destructive_hits']
        NEW_REVIEWED_DESTRUCTIVE_CALLS=rows['NEW_REVIEWED_DESTRUCTIVE_CALLS']
        NEW_REVIEWED_RATIONALES=rows['NEW_REVIEWED_RATIONALES']
        reviewed={
          'devtool/actions_dn03_dotnet.py':('path.unlink(missing_ok=True)',),
          'devtool/actions_dn03_r3.py':('path.unlink(missing_ok=True)',),
          'devtool/actions_dn03_r4.py':('path.unlink(missing_ok=True)',),
          'devtool/dn03_runtime_only.py':('archive.unlink(missing_ok=True)','shutil.rmtree(stage)'),
        }
        hits=_destructive_hits()
        for name, expected in reviewed.items():
            self.assertEqual(NEW_REVIEWED_DESTRUCTIVE_CALLS[name],expected)
            self.assertEqual(tuple(line for _,line in hits[name]),expected)
            self.assertGreaterEqual(len(NEW_REVIEWED_RATIONALES[name]),40)
        self.assertIn('race',NEW_REVIEWED_RATIONALES['devtool/dn03_runtime_only.py'])

    def test_job_env_scope_only_matches_job_level(self):
        # Extract the pure helper without importing heavyweight test dependencies
        # (pytest and Devtool fixture plugins are only required on hosted CI).
        source=ast.parse((ROOT/'tests/test_rm06_graph_export.py').read_text())
        helper=next(n for n in source.body
                    if isinstance(n,ast.FunctionDef) and n.name=='_rm06_job_env_blocks')
        namespace={}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[helper],type_ignores=[])),
                     '<rm06-helper>', 'exec'), namespace)
        _rm06_job_env_blocks=namespace['_rm06_job_env_blocks']
        text=('jobs:\n  native:\n    env:\n      PIN: x\n    steps:\n'
              '      - name: step\n        env:\n          STORE: ${{ runner.temp }}\n')
        result=_rm06_job_env_blocks(text)
        self.assertEqual(len(result),1)
        self.assertFalse(any('${{ runner.' in env for env in result))
        bad=text.replace('      PIN: x','      PIN: ${{ runner.temp }}')
        self.assertTrue(any('${{ runner.' in env for env in _rm06_job_env_blocks(bad)))

if __name__=='__main__':unittest.main(verbosity=2)
