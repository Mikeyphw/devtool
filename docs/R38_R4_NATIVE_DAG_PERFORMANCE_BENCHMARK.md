# R38-R4 — Native DAG scheduling and scratch-routing benchmark

This is a **measurement-only, non-release** addition. It invokes the actual
`workflow_scheduler.run_workflow_specs` admission logic with deterministic
synthetic in-process workers, not Gradle, Go, .NET, Android, Python package
builds, or real cache reuse. It does not introduce another scheduler.

## Reproduce

```bash
python -m devtool.workflow_benchmark --nodes 24 --repeats 5 --workers 4 --work-ms 4 --shape fanout_join --json > r38-r4.json
```

Compare four fixed cases on the same host: serial and 4-worker operation,
each with the existing scratch-isolation mode `off` and `enforce`. Each
repetition executes the native scheduler with an independent fanout/join,
fanout-only, or dependency-chain workload. A cache-environment probe performs
a small per-node write to the isolated `XDG_CACHE_HOME` and checks the
existing owned-root cleanup. This measures **scratch/cache routing overhead**,
not build cache hit rates, correctness or warm/cold compilation.

Receipts include measured wall-clock samples (median, p95, min, max), observed
peak concurrent synthetic workers, node elapsed aggregates, source Git commit
and tree when available, Python/platform metadata, and explicit negative
qualification. The benchmark refuses malformed/oversized requests and fails
when observed node outcomes/worker bounds or scratch cleanup violate contracts.
Results are not normalized to another host or Python version.

## Hosted lane

Manual workflow: `.github/workflows/devtool-r38-dag-benchmark.yml`.
It uploads source-bound machine-readable samples for fanout/join and chain
shapes, with `real_build=false`, `real_cache_hit=false`, `device=false`,
`release=false`. The existing full Python regression independently tests the
benchmark schema, limits, scratch cleanup and source contracts.

No blanket elapsed-time pass/fail threshold is used: runner contention and
machine load can change timing. Compare distributions on **same-class hosts**
across separate runs before setting a meaningful regression budget. A faster
synthetic DAG is not evidence of faster real Android/Gradle builds.

## Outstanding work

Real-host per-provider cold/warm cache metrics and bottleneck attribution,
reliable orphan process recovery, device proof, installed-wheel smoke, and
formal release qualification require separate evidence.
