# devtool 0.5.7

- Fix Termux/chroot repository namespace mismatches.
- Automatically delegate the full devtool process when `-r` exists only inside the selected chroot.
- Use an explicit repository as the chroot startup directory instead of inheriting an unrelated Termux cwd.
- Probe and repair the default Termux-home mapping when a repository is mounted under another chroot user, such as `/home/mike` instead of `/home/termux`.
- Treat explicit `--chroot` on Termux as the supported host/chroot backend path rather than the generic Linux wrapper.
- Keep Android RUN_COMMAND working directories Termux-visible; chroot-only `/home/...` repository paths remain in the devtool command rather than being passed as the Termux workdir.
- Bump Android HUD source to 1.5.6.
