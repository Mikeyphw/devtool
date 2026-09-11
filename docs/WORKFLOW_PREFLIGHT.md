# Expanded workflow toolchain preflight (MP08)

MP08 analyzes what the **expanded executable workflow graph** actually needs instead of assuming a target has only one toolchain.

```bash
devtool preflight --target api --workflow validate
devtool preflight --group release --workflow validate
devtool preflight --all-targets
devtool preflight --affected --workflow validate
devtool doctor --group release --workflow all --json
```

The same analyzer feeds `preflight` and the workflow section of `doctor`.

## What is aggregated

For every selected target/action invocation Devtool expands cross-target references and reusable jobs, then reports:

- all runners used by executable nodes;
- actual command executables/tools produced by those runners/jobs;
- effective execution environments per node;
- target and job roots;
- declared output/write scopes;
- expanded graph nodes and target/action invocations.

This means a Python product that runs Node code generation before pytest is reported as requiring both Python and Node. An Android target may legitimately aggregate Python, Node, Go, Gradle, Android SDK, or other runner-produced executables without changing the target's primary runner identity.

## Environment-aware availability

A tool is checked on the local host only when the node executes in the current environment. For SSH/chroot/PRoot/other destination execution, host PATH absence is not treated as proof the destination tool is missing; the tool is marked `deferred` and normal destination preflight verifies transport/environment readiness. This avoids false negatives such as reporting a remote-only compiler missing because it is not installed in Termux.

Execution-environment inheritance follows runtime semantics: a reusable job with `execution_environment = "auto"` inherits the referenced target's effective environment, chroot, and device settings.

## Safety checks

Preflight reports failures for conditions including:

- missing target or reusable-job roots;
- unregistered/unsupported runners when encountered by the graph;
- unavailable local tools;
- failed execution-environment/transport preflight;
- workflow planning errors;
- declared target/job output scopes escaping the repository.

Explicit shell jobs are reported as opaque because Devtool cannot reliably infer every executable hidden inside shell syntax. Structured argv remains preferred:

```toml
command = ["npm", "run", "generate"]
```

rather than:

```toml
shell_command = "npm run generate && python finish.py"
```

## Machine output

`devtool preflight --json` returns a structured report containing `targets`, `invocations`, `runners`, `tools`, `environments`, `checks`, and `graph_nodes`. `devtool doctor --json` embeds the same structure as `workflow_preflight` and incorporates its health into the doctor's aggregate `healthy` field.

A `deferred` destination-only tool or an `opaque` shell command is informational; statuses such as `missing`, `error`, `unsafe`, `unsupported`, or `incompatible` make the workflow preflight unhealthy.
