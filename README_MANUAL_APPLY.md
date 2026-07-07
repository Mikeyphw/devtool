Unzip this at the root of the devtool repo.

```bash
cd "$HOME/Code/sims3tools"
unzip -o "$HOME/Downloads/devtool_termux_host_chroot_toolchain_backend_manual.zip"
PYTHONPATH=. python -m pytest tests/test_termux_chroot_runner.py tests/test_cli.py -q
```
