# Plugin architecture

Devtool exposes plugin API version 1 through `devtool.plugins`. Built-in descriptors cover Android, Python, Rust, and Magisk/KernelSU, while third-party plugins are discovered from the `devtool.plugins` Python entry-point group.

External load failures are isolated and surfaced by discovery. A descriptor with an unsupported API version is rejected without preventing other plugins from loading. `plugin list` and `plugin doctor NAME` are the stable CLI surfaces.
