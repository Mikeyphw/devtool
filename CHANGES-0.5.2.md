# devtool 0.5.2

- Fix the Qt Control Center Debian packaging build repeatedly re-running CMake.
- Move default Qt build output outside the source tree under the XDG cache directory.
- Use a clean, dedicated out-of-source build for Debian packages.
- Normalize future-dated source mtimes before configuring, preventing Ninja manifest clock-skew loops after extracting archives.
- Reject and recreate CMake caches configured for a different source directory.
- Update Devtool Control Center to 0.3.2.
