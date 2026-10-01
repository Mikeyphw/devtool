# PRES-H01 — Textual main-thread safety hotfix

## Incident

After PRES-R01 v2 qualified on native Termux, real artifact validation repeatedly
raised `ValueError: signal only works in main thread of the main interpreter` from
Textual 8.2.8 `LinuxDriver`. The R01 presenter had started `ExecutionProgressApp.run()`
in a daemon helper thread.

## Runtime correction

- Background terminal-driver startup is forbidden.
- The current synchronous execution controller fails closed to plain streaming when
  its default Textual terminal presenter has no main-thread host.
- Semantic progress, runner output capture, durable logs, Tasker/HUD/EXO evidence,
  timeout handling and cancellation authority remain unchanged.
- No Rich Live ownership is restored.
- The concrete `ExecutionProgressApp` and Pilot coverage remain available for the
  forthcoming main-thread host.
- Worker-thread regression coverage reproduces the artifact-validation context and
  requires zero `devtool-textual-execution` terminal-driver threads.

## Campaign disposition

This is an in-place PRES-R01 incident hotfix and consumes no new roadmap position.
PRES-R01-P003 is reopened; the two-step Textual cancellation and bounded queue bridge
remain implemented but are not considered live-qualified until the main-thread host
is delivered. PRES-R02/R03/G2R remain separate.

DIST-R01 is already qualified and applied and remains outside PRES ownership.
