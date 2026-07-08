# Manual apply

Unzip this archive at the root of the devtool source repo, then run the focused tests.

```bash
cd "$HOME/Code/devtool"
unzip -o "$HOME/Downloads/devtool_chroot_dotnet_progress_parser_fix_manual.zip"
PYTHONPATH=. python -m pytest \
  tests/test_termux_chroot_runner.py \
  tests/test_dotnet_progress.py \
  tests/test_runners_termux.py \
  tests/test_process_hang.py \
  -q
```
