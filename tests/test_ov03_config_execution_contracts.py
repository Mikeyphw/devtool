from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool.android_build import devtool_cache_dir
from devtool.android_integration import _android_runtime_dir
from devtool.build_optimization import (
    _bounded_makeflags,
    _cache_root,
    resolve_android_validation_phase_policy,
)
from devtool.cache_cleanup import cleanup_devtool_caches, repository_cache_key
from devtool.cli import _execution_plan_for_args, _prepare_specs_for_execution, _resolve_config_editor
from devtool.config import load_project_config
from devtool.desktop.repository_store import _desktop_state_dir
from devtool.execution_environments import CHROOT, NATIVE_TERMUX, preflight_execution
from devtool.models import CommandSpec, ProjectConfig
from devtool.single_run import SingleRunLock, lock_path
from devtool.termux_chroot_runner import ChrootIdentity, resolve_chroot_identity, wrap_specs_for_termux_host_chroot
from devtool.xdg import cache_dir, data_dir, state_dir


class Ov03ConfigExecutionContracts(unittest.TestCase):
    @staticmethod
    def args(**values: object) -> argparse.Namespace:
        defaults: dict[str, object] = {
            "host": False,
            "chroot": "",
            "execution_environment": "auto",
            "matrix": "",
            "matrix_mode": "rid",
            "dry_run": False,
            "no_auto_chroot": False,
            "instrumented": False,
            "proot_distro": "",
            "shared_storage_policy": "allow",
            "allow_shared_storage_build": False,
            "_devtool_termux_host_chroot_backend": False,
            "_devtool_chroot_path_map": None,
            "_devtool_chroot_name": "",
        }
        defaults.update(values)
        return argparse.Namespace(**defaults)

    def test_xdg_standard_relative_values_are_ignored_and_custom_relative_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            env = {
                "HOME": str(home),
                "XDG_STATE_HOME": "relative-state",
                "XDG_DATA_HOME": "relative-data",
                "XDG_CACHE_HOME": "relative-cache",
            }
            self.assertEqual(state_dir(env), home / ".local/state/devtool")
            self.assertEqual(data_dir(env), home / ".local/share/devtool")
            self.assertEqual(cache_dir(env), home / ".cache/devtool")
            with self.assertRaises(ValueError):
                state_dir({"HOME": str(home), "DEVTOOL_XDG_DIR": "relative"})

    def test_devtool_xdg_dir_is_authoritative_across_cli_android_and_desktop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = str((Path(tmp) / "isolated").resolve())
            env = {"HOME": tmp, "DEVTOOL_XDG_DIR": root}
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(state_dir(), Path(root))
                self.assertEqual(data_dir(), Path(root))
                self.assertEqual(cache_dir(), Path(root))
                self.assertEqual(_android_runtime_dir(), Path(root))
                self.assertEqual(devtool_cache_dir(), Path(root))
                self.assertEqual(_desktop_state_dir(), Path(root) / "desktop")

    def test_config_missing_directory_malformed_table_and_bad_execution_are_structured_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(SystemExit, "does not exist"):
                load_project_config(str(root), str(root / "missing.toml"))
            with self.assertRaisesRegex(SystemExit, "not a file"):
                load_project_config(str(root), str(root))
            config = root / ".devtool.toml"
            config.write_text('project = "wrong"\n', encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "must be a TOML table"):
                load_project_config(str(root), str(config))
            config.write_text('[project]\nkind="python"\n[execution]\nenvironment="space-station"\n', encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "Invalid execution environment"):
                load_project_config(str(root), str(config))

    def test_global_defaults_can_be_cleared_by_explicit_empty_repo_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            global_cfg = Path(tmp) / "global.toml"
            global_cfg.write_text('[gradle]\nargs=["--scan"]\n', encoding="utf-8")
            (root / ".devtool.toml").write_text('[project]\nkind="python"\n[gradle]\nargs=[]\n', encoding="utf-8")
            with patch.dict(os.environ, {"HOME": tmp, "DEVTOOL_GLOBAL_CONFIG": str(global_cfg)}, clear=True):
                config = load_project_config(str(root), None)
            self.assertEqual(config.runner_options["gradle"]["args"], [])

    def test_execution_provenance_records_target_and_environment_winners(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / ".devtool.toml"
            cfg.write_text(
                '[project]\nkind="python"\ndefault_target="app"\nexecution_environment="desktop-linux"\n'
                '[execution]\nenvironment="native-termux"\nchroot_name="global-chroot"\n'
                '[targets.app]\ntype="python"\nroot="."\nexecution_environment="chroot"\nchroot_name="target-chroot"\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"HOME": tmp}, clear=True):
                config = load_project_config(str(root), None)
            self.assertEqual(config.execution_environment, "chroot")
            self.assertEqual(config.chroot_name, "target-chroot")
            self.assertEqual(config.config_provenance["execution_environment"], "target")
            self.assertEqual(config.config_provenance["chroot_name"], "target")

    def test_explicit_empty_repo_execution_values_clear_lower_precedence_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            global_cfg = Path(tmp) / "global.toml"
            global_cfg.write_text('[termux_chroot]\ndefault_chroot="global-one"\n', encoding="utf-8")
            (root / ".devtool.toml").write_text(
                '[project]\nkind="python"\n[execution]\nenvironment=""\nchroot_name=""\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {
                "HOME": tmp,
                "DEVTOOL_GLOBAL_CONFIG": str(global_cfg),
                "DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT": "chroot",
                "DEVTOOL_TERMUX_CHROOT": "env-two",
            }, clear=True):
                config = load_project_config(str(root), None)
        self.assertEqual(config.execution_environment, "auto")
        self.assertEqual(config.chroot_name, "androidos")
        self.assertEqual(config.config_provenance["execution_environment"], "execution")
        self.assertEqual(config.config_provenance["chroot_name"], "execution")

    def test_explicit_empty_target_values_clear_repo_and_environment_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".devtool.toml").write_text(
                '[project]\nkind="python"\ndefault_target="app"\n'
                '[execution]\nenvironment="chroot"\nchroot_name="repo-chroot"\n'
                '[targets.app]\ntype="python"\nroot="."\nexecution_environment=""\nchroot_name=""\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {
                "HOME": tmp,
                "DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT": "desktop-linux",
                "DEVTOOL_TERMUX_CHROOT": "env-two",
            }, clear=True):
                config = load_project_config(str(root), None)
        self.assertEqual(config.execution_environment, "auto")
        self.assertEqual(config.chroot_name, "androidos")
        self.assertEqual(config.config_provenance["execution_environment"], "target")
        self.assertEqual(config.config_provenance["chroot_name"], "target")

    def test_terminal_preference_is_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "global.toml"
            cfg.write_text('[terminal]\npreferred="custom-editor --wait"\n', encoding="utf-8")
            with patch.dict(os.environ, {"HOME": tmp, "DEVTOOL_GLOBAL_CONFIG": str(cfg)}, clear=True), \
                    patch("devtool.cli.shutil.which", side_effect=lambda name: "/usr/bin/custom-editor" if name == "custom-editor" else None):
                self.assertEqual(_resolve_config_editor(), ["custom-editor", "--wait"])

    def test_no_auto_chroot_prevents_android_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(repo_root=Path(tmp), kind="android", execution_environment="auto", chroot_name="androidos")
            env = {"HOME": tmp, "PREFIX": "/data/data/com.termux/files/usr", "TERMUX_VERSION": "1"}
            with patch.dict(os.environ, env, clear=True), patch("devtool.android_integration.should_prefer_android_chroot", return_value=True):
                plan = _execution_plan_for_args(config, self.args(no_auto_chroot=True))
            self.assertEqual(plan.effective, NATIVE_TERMUX)

    def test_explicit_chroot_cannot_fall_back_to_desktop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(repo_root=Path(tmp), kind="python", execution_environment="auto", chroot_name="androidos")
            with patch.dict(os.environ, {"HOME": tmp}, clear=True):
                plan = _execution_plan_for_args(config, self.args(chroot="androidos"))
            self.assertEqual(plan.effective, CHROOT)
            self.assertTrue(preflight_execution(config.repo_root, plan).failures())

    def test_unmapped_selected_target_fails_before_command_launch(self) -> None:
        config = ProjectConfig(
            repo_root=Path("/data/data/com.termux/files/home/Code/repo"),
            project_root="/data/data/com.termux/files/usr/tmp/unmapped",
            restore_target="/data/data/com.termux/files/usr/tmp/unmapped",
            build_target="/data/data/com.termux/files/usr/tmp/unmapped",
            test_target="/data/data/com.termux/files/usr/tmp/unmapped",
            kind="python",
            execution_environment="chroot",
            chroot_name="androidos",
        )
        env = {"HOME": "/data/data/com.termux/files/home", "PREFIX": "/data/data/com.termux/files/usr", "TERMUX_VERSION": "1"}
        with patch.dict(os.environ, env, clear=True), \
                patch("devtool.execution_environments.shutil.which", return_value="/usr/bin/chrootctl"):
            with self.assertRaisesRegex(SystemExit, "preflight failed"):
                _prepare_specs_for_execution(config, [CommandSpec("test", ["python3", "-V"])], self.args())

    def test_chroot_wrapper_clears_host_environment_and_rebuilds_guest_identity(self) -> None:
        spec = CommandSpec(
            "test",
            ["python3", "-V"],
            env={"PATH": "/data/data/com.termux/files/usr/bin", "JAVA_HOME": "/data/data/com.termux/files/usr/lib/jvm/x", "KEEP": "yes"},
            cwd="/data/data/com.termux/files/home/Code/demo",
        )
        env = {
            "HOME": "/data/data/com.termux/files/home",
            "DEVTOOL_TERMUX_CHROOT_HOME": "/home/alice",
            "DEVTOOL_TERMUX_CHROOT_USER": "alice",
        }
        identity = ChrootIdentity("alice", "/home/alice", "/bin/zsh")
        with patch("devtool.termux_chroot_runner.resolve_chroot_identity", return_value=identity):
            wrapped = wrap_specs_for_termux_host_chroot(
                [spec], "androidos", repo_root=spec.cwd, env=env,
                path_map=[(env["HOME"], "/home/alice")],
            )[0]
        self.assertEqual(wrapped.env, {})
        command = wrapped.argv[-1]
        self.assertIn("exec env -i", command)
        self.assertIn("HOME=/home/alice", command)
        self.assertIn("runuser -u alice", command)
        self.assertIn("PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", command)
        self.assertNotIn("/data/data/com.termux/files/usr/lib/jvm", command)
        self.assertIn("KEEP=yes", command)

    def test_chroot_identity_is_proved_from_guest_passwd_not_host_username(self) -> None:
        completed = __import__("subprocess").CompletedProcess(
            ["chrootctl"], 0, "alice\t/home/alice\t/bin/zsh\n", ""
        )
        env = {"HOME": "/data/data/com.termux/files/home", "DEVTOOL_CHROOT_SUDO": ""}
        mappings = [(env["HOME"], "/home/alice")]
        with patch("devtool.termux_chroot_runner.subprocess.run", return_value=completed) as run:
            identity = resolve_chroot_identity("androidos", mappings, env=env)
        self.assertEqual(identity, ChrootIdentity("alice", "/home/alice", "/bin/zsh"))
        self.assertIn("/etc/passwd", run.call_args.args[0][-1])

    def test_global_chroot_default_does_not_mask_environment_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / ".devtool.toml").write_text('[project]\nkind="python"\n', encoding="utf-8")
            global_cfg = Path(tmp) / "global.toml"
            global_cfg.write_text('[termux_chroot]\ndefault_chroot="global-one"\n', encoding="utf-8")
            with patch.dict(os.environ, {
                "HOME": tmp,
                "DEVTOOL_GLOBAL_CONFIG": str(global_cfg),
                "DEVTOOL_TERMUX_CHROOT": "env-two",
            }, clear=True):
                config = load_project_config(str(root), None)
        self.assertEqual(config.chroot_name, "env-two")
        self.assertEqual(config.config_provenance["chroot_name"], "environment")

    def test_repo_lock_path_is_independent_of_runtime_directory_and_lock_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            state = Path(tmp) / "state"
            env1 = {"HOME": tmp, "XDG_STATE_HOME": str(state), "XDG_RUNTIME_DIR": str(Path(tmp) / "run-a")}
            env2 = {"HOME": tmp, "XDG_STATE_HOME": str(state), "XDG_RUNTIME_DIR": str(Path(tmp) / "run-b")}
            with patch.dict(os.environ, env1, clear=True):
                first_path = lock_path(repo)
                first = SingleRunLock(repo).acquire()
            try:
                with patch.dict(os.environ, env2, clear=True):
                    self.assertEqual(lock_path(repo), first_path)
                    with self.assertRaises(BlockingIOError):
                        SingleRunLock(repo).acquire()
            finally:
                first.release()

    def test_cache_cleanup_preserves_other_repo_durable_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            repo = root / "repo-a"
            repo.mkdir()
            key = repository_cache_key(repo)
            disposable = cache / "build" / key / "chroot" / "python"
            disposable.mkdir(parents=True)
            (disposable / "junk").write_text("x", encoding="utf-8")
            state = root / "state"
            share = root / "share"
            active = state / "locks/repo-a/run.lock"
            journal = share / "transactions/repo-a/tx/transaction.json"
            log = state / "logs/run.log"
            for item in (active, journal, log):
                item.parent.mkdir(parents=True, exist_ok=True)
                item.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "requires repo_root"):
                cleanup_devtool_caches(cache_root=cache, state_root=state, share_root=share)
            cleanup_devtool_caches(
                cache_root=cache, state_root=state, share_root=share,
                repo_root=repo, backend="chroot",
            )
            self.assertFalse(disposable.exists())
            self.assertEqual(active.read_text(), "keep")
            self.assertEqual(journal.read_text(), "keep")
            self.assertEqual(log.read_text(), "keep")

    def test_cache_cleanup_is_scoped_by_repository_and_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            repo_a = root / "repo-a"; repo_b = root / "repo-b"
            repo_a.mkdir(); repo_b.mkdir()
            key_a = repository_cache_key(repo_a); key_b = repository_cache_key(repo_b)
            cache_a = cache / "build" / key_a / "chroot" / "python"
            cache_b = cache / "build" / key_b / "chroot" / "python"
            cache_other_backend = cache / "build" / key_a / "desktop-linux" / "python"
            for path in (cache_a, cache_b, cache_other_backend):
                path.mkdir(parents=True)
                (path / "marker").write_text("x", encoding="utf-8")
            state = root / "state"
            active_b = state / "locks" / key_b / "run.lock"
            active_b.parent.mkdir(parents=True)
            active_b.write_text("active", encoding="utf-8")
            removed = cleanup_devtool_caches(
                cache_root=cache,
                state_root=state,
                repo_root=repo_a,
                backend="chroot",
            )
            self.assertIn(str(cache / "build" / key_a / "chroot"), removed)
            self.assertFalse(cache_a.exists())
            self.assertTrue(cache_b.exists())
            self.assertTrue(cache_other_backend.exists())
            self.assertEqual(active_b.read_text(), "active")

    def test_relative_build_cache_root_is_resolved_from_project_not_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            project = repo / "app"
            elsewhere = Path(tmp) / "elsewhere"
            project.mkdir(parents=True)
            elsewhere.mkdir()
            config = ProjectConfig(
                repo_root=repo,
                project_root=str(project),
                kind="android",
                runner_options={"build": {"cache_root": "cache-dir"}},
            )
            old = Path.cwd()
            try:
                os.chdir(elsewhere)
                resolved = _cache_root(config, "android", "desktop-linux")
                self.assertTrue(resolved.is_relative_to(project / "cache-dir"))
                self.assertFalse(resolved.is_relative_to(elsewhere))
            finally:
                os.chdir(old)

    def test_android_phase_overrides_cannot_raise_worker_cpu_or_heap_caps(self) -> None:
        config = ProjectConfig(
            repo_root=Path.cwd(),
            kind="android",
            execution_environment="native-termux",
            runner_options={
                "validation": {
                    "android_safe": True,
                    "max_workers": 2,
                    "cpu_limit": 2,
                    "gradle_heap_mb": 1024,
                    "phase_profiles": {"lint": {"max_workers": 99, "cpu_limit": 99, "gradle_heap_mb": 99999}},
                }
            },
        )
        with patch("devtool.build_optimization.os.cpu_count", return_value=8), patch("devtool.build_optimization.memory_info_mb", return_value=(4096, 8192)):
            lint = resolve_android_validation_phase_policy(config, "lint")
        self.assertLessEqual(lint.max_workers, 2)
        self.assertLessEqual(lint.cpu_limit, 2)
        self.assertLessEqual(lint.gradle_heap_mb, 1024)

    def test_makeflags_preserves_unrelated_user_flags(self) -> None:
        value = _bounded_makeflags("-k --warn-undefined-variables -j8 --output-sync", 2)
        self.assertIn("-k", value)
        self.assertIn("--warn-undefined-variables", value)
        self.assertIn("--output-sync", value)
        self.assertIn("-j2", value)
        self.assertNotIn("-j8", value)

    def test_android_sources_remove_fixed_identity_and_direct_root_fallback(self) -> None:
        root = Path(__file__).resolve().parents[1] / "android/devtool-live-hud/app/src/main/java/io/github/mikeceead/devtoolhud"
        picker = (root / "RepositoryPickerActivity.java").read_text(encoding="utf-8")
        plan = (root / "RootExecutionPlan.java").read_text(encoding="utf-8")
        broker = (root / "RootCommandBroker.java").read_text(encoding="utf-8")
        store = (root / "ValidationStore.java").read_text(encoding="utf-8")
        controller = (root / "ValidationController.java").read_text(encoding="utf-8")
        detail = (root / "RepositoryDetailActivity.java").read_text(encoding="utf-8")
        path_resolver = (root / "RepositoryPathResolver.java").read_text(encoding="utf-8")
        doctor = (root / "RepositoryDoctor.java").read_text(encoding="utf-8")
        self.assertNotIn('/home/mike', picker)
        self.assertNotIn('value(EXTRA_USER, "mike")', picker)
        self.assertIn('linux_home', broker)
        self.assertIn('!plan.directRootFallback', plan)
        self.assertIn('!plan.usable || plan.directRootFallback', broker)
        self.assertIn(r'if [ -z \"$linux_user\" ]; then kv linux_home', broker)
        self.assertIn("kv user_switch_method unavailable", broker)
        self.assertIn("exit 69", broker)
        self.assertIn('value.executionEnvironment.equals("native-termux") ? "termux" : ""', store)
        self.assertIn("session.executorLinuxUser", controller)
        self.assertIn("session.executorWorkdir", controller)
        self.assertIn("runuser -u", controller)
        self.assertIn("Unable to switch to requested chroot Linux user", controller)
        self.assertNotIn('exec -- zsh -ilc " + shell(command)', controller)
        self.assertIn("Chroot Linux user is unavailable", detail)
        self.assertIn("runuser -u", detail)
        self.assertIn(r'SHELL=\"$login_shell\"', detail)
        self.assertIn(r'exec \"$SHELL\" -l', detail)
        self.assertIn('Chroot Linux user is not configured', (root / "RepositoryDoctorResult.java").read_text(encoding="utf-8"))
        self.assertIn(r'runuser -u \"$linux_user\" -- git -C', doctor)
        self.assertIn(r'su - \"$linux_user\" -s \"$selected_shell\" -c', doctor)
        self.assertNotIn(r'exec \"$login_shell\" -l', detail)


if __name__ == "__main__":
    unittest.main()
