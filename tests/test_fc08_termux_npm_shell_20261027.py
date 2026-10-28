"""FC08 native npm shell: no forged host and no dropped npm validation."""
from __future__ import annotations
import os
from pathlib import Path
import tempfile
from unittest import TestCase, mock
from devtool import build_optimization as b

class TermuxNpmShellContract(TestCase):
    def test_trusted_prefix_shell_is_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'bin').mkdir()
            shell=root/'bin/sh';shell.write_text('#!/bin/sh\nexit 0\n');shell.chmod(0o700)
            self.assertEqual(b._verified_termux_npm_script_shell(str(root),canonical_prefix=root),str(shell))
            self.assertIsNone(b._verified_termux_npm_script_shell('/usr',canonical_prefix=root))
            self.assertIsNone(b._verified_termux_npm_script_shell('relative',canonical_prefix=root))
    def test_no_shell_and_no_untrusted_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'bin').mkdir()
            self.assertIsNone(b._verified_termux_npm_script_shell(str(root),canonical_prefix=root))
            f=root/'bin/sh';f.write_text('not executable');f.chmod(0o600)
            self.assertIsNone(b._verified_termux_npm_script_shell(str(root),canonical_prefix=root))
    def test_no_env_override_or_global_shell_mutation(self):
        src=(Path(__file__).parents[1]/'devtool/build_optimization.py').read_text()
        self.assertIn('"NPM_CONFIG_SCRIPT_SHELL"',src)
        self.assertIn('policy.environment == NATIVE_TERMUX',src)
        self.assertIn('detect_current_environment() == NATIVE_TERMUX',src)
        self.assertNotIn('os.environ["npm_config_script_shell"]',src)
    def test_node_build_and_lifecycle_are_not_bypassed(self):
        src=(Path(__file__).parents[1]/'devtool/project_runners.py').read_text()
        self.assertIn('["npm", "run", script]',src)
        self.assertIn('specs.extend(self.build_specs(config, "restore"))',src)
        self.assertIn('specs.extend(self.build_specs(config, "build"))',src)


class TermuxNpmNotifierContract(TestCase):
    """Production Node plans must honor the native reproduced notifier boundary."""

    @staticmethod
    def _env(environment='native-termux', base=None, ambient=None, detected='native-termux'):
        from types import SimpleNamespace
        policy = SimpleNamespace(environment=environment, max_workers=2, cache_root=Path('/tmp/devtool-npm-policy'))
        with mock.patch.object(b, 'resolve_build_policy', return_value=policy), \
             mock.patch.object(b, 'detect_current_environment', return_value=detected), \
             mock.patch.dict(os.environ, ambient or {}, clear=True):
            return b.optimized_build_env(object(), 'node', base)

    def test_only_actual_native_termux_defaults_notifier_off(self):
        env = self._env()
        self.assertEqual(env['npm_config_update_notifier'], 'false')
        self.assertEqual(env['npm_config_cache'], '/tmp/devtool-npm-policy/npm')
        self.assertEqual(env['npm_config_audit'], 'false')
        self.assertEqual(env['npm_config_fund'], 'false')
        for declared, actual in [('desktop-linux','desktop-linux'), ('chroot','chroot'),
                                 ('native-termux','desktop-linux'), ('desktop-linux','native-termux')]:
            self.assertNotIn('npm_config_update_notifier',
                self._env(environment=declared, detected=actual))
        self.assertNotIn('npm_config_update_notifier', self._other_kind_env())

    @staticmethod
    def _other_kind_env():
        from types import SimpleNamespace
        policy = SimpleNamespace(environment='native-termux', max_workers=2, cache_root=Path('/tmp/devtool-npm-policy'))
        with mock.patch.object(b, 'resolve_build_policy', return_value=policy), \
             mock.patch.object(b, 'detect_current_environment', return_value='native-termux'), \
             mock.patch.dict(os.environ, {}, clear=True):
            return b.optimized_build_env(object(),'python',{})

    def test_operator_explicit_project_or_ambient_precedence(self):
        for key in ('npm_config_update_notifier', 'NPM_CONFIG_UPDATE_NOTIFIER'):
            self.assertNotIn('npm_config_update_notifier', self._env(ambient={key:'true'}))
            result=self._env(base={key:'true'})
            if key == 'npm_config_update_notifier':
                self.assertEqual(result[key], 'true')
            else:
                self.assertEqual(result[key], 'true')
                self.assertNotIn('npm_config_update_notifier',result)
        self.assertEqual(self._env(base={'npm_config_update_notifier':'false'})['npm_config_update_notifier'],'false')

    def test_no_global_mutation_or_validation_suppression(self):
        with mock.patch.dict(os.environ, {'SENTINEL': 'stable'}, clear=True):
            before=dict(os.environ)
            self._env()
            self.assertEqual(os.environ,before)
        src=(Path(__file__).parents[1]/'devtool/project_runners.py').read_text()
        for text in ('["npm", "run", script]', 'specs.extend(self.build_specs(config, "restore"))',
                     'specs.extend(self.build_specs(config, "build"))',
                     'specs.extend(self.build_specs(config, "test"))'):
            self.assertIn(text,src)

class CaptureMainBranchContract(TestCase):
    def test_capture_is_rebased_to_canonical_main(self):
        src=(Path(__file__).parents[1]/'tools/fc08_host_capture.py').read_text()
        self.assertIn("BRANCH='main'",src)
        self.assertIn("BASE='dc23352e8f9d3ce3370ce82b88e067bd6b10deae'",src)
        self.assertNotIn("BRANCH='feature/rmp01-security-remediation'",src)
        self.assertIn('REFUSED_REMOTE_SOURCE_TREE_DRIFT',src)
