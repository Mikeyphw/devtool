# PRES-G2 pre-gate interaction/reliability remediation

**Campaign boundary:** remediation discovered by the PRES-G2 54/70 audit; this is not the gate and does not consume a full-plan position.  
**Base:** qualified PRES-X08 (`4885431`).  
**Next:** distinct PRES-G2 interaction/reliability milestone gate.

## Audit findings folded into this implementation boundary

The X08 control-center projection was sound but the concrete operator shell still left several G2 promises unprovable. This remediation closes those gaps without moving domain authority into PRES:

- adds the missing Overlays page and collision-free `o` navigation;
- makes page/domain authorities explicit for RUN/EXO/LIFE/DIST/TEST/EXEC/WRAP/validation;
- adds panel-local degradation/recovery instead of global TUI failure;
- makes visible actions registry-backed with explicit disabled reasons;
- rejects unknown/guessed action strings fail-closed;
- separates Ctrl+C cancellation from q/Esc exit and routes cancel through the canonical action request contract;
- adds an explicit terminal restoration lifetime guard around the interactive renderer;
- binds Textual navigation/cancel/help actions to the control-center model instead of dead action names;
- keeps `--once` renderer-independent and read-only;
- renders bounded plain text from the same snapshot contract for degraded/plain operation.

No execution scheduler, RunStore, artifact transaction engine, SSH worker engine, test selector, or shell-command action layer is introduced.
