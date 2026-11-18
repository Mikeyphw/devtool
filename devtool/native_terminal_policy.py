"""Terminal-first routing policy shared by apply and execution selectors.

Ordinary Devtool invocations do not start an alternate-screen host.  A user
may deliberately opt in with ``--ui compact``/``--ui full`` (or a supported
host toggle).  This module owns presentation selection only, never execution,
validation, session identity, or the remote mutation authorities.
"""
from __future__ import annotations

from typing import Any, Mapping

_ENABLE = frozenset({"1", "true", "yes", "on"})
_DISABLE = frozenset({"0", "false", "no", "off", "plain", "disabled"})
_TEXTUAL = frozenset({"compact", "full", "textual"})


def explicit_textual_host_requested(
    args: Any, environment: Mapping[str, str], *, host_key: str
) -> bool:
    """Fail closed unless the invocation explicitly selected a Textual UI.

    The ordinary command can render Rich *inline* independently of this gate.
    The existing opt-out flags remain authoritative even for --ui full.
    """
    host = str(environment.get(host_key, "") or "").strip().lower()
    selected = str(getattr(args, "job_ui", "") or "").strip().lower()
    env_selected = str(environment.get("DEVTOOL_JOB_UI", "") or "").strip().lower()
    if host in _DISABLE or selected in _DISABLE or env_selected in _DISABLE:
        return False
    if str(environment.get("DEVTOOL_EXECUTION_PRESENTATION", "") or "").strip().lower() == "plain":
        return False
    return host in _ENABLE or selected in _TEXTUAL or env_selected in _TEXTUAL
