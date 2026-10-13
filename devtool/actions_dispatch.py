"""ACTIONS-02: explicit and source-bound GitHub workflow dispatch.

Run tracking and remote execution qualification belong to ACTIONS-03.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
from urllib.parse import quote, urlsplit
from typing import Any, Mapping

from .github_actions import GitHubActionsClient, GitHubActionsError, GitHubDispatchOutcomeUnknown, _slug

_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,199}\Z")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,99}\Z")
_SHA = re.compile(r"[a-fA-F0-9]{40}\Z")
_CONTENT_LIMIT = 256 * 1024


def _ref(value: str) -> str:
    if (not _REF.fullmatch(value) or value.endswith("/") or "//" in value
            or any(part in {".", ".."} for part in value.split("/")) or value.endswith(".lock")
            or value.startswith("refs/") or "@{" in value):
        raise GitHubActionsError("Ref must be a safe branch or tag name")
    return value


def _workflow_path(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml", value):
        raise GitHubActionsError("Workflow must reside in .github/workflows with a YAML filename")
    return value


def _parse_workflow_yaml(content: bytes) -> dict[str, Any]:
    if len(content) > _CONTENT_LIMIT:
        raise GitHubActionsError("Workflow YAML exceeds bounded inspection size")
    try:
        import yaml
    except ImportError:
        raise GitHubActionsError("PyYAML is required to validate workflow_dispatch input definitions") from None
    try:
        doc = yaml.safe_load(content.decode("utf-8"))
    except (ValueError, UnicodeError, yaml.YAMLError):
        raise GitHubActionsError("Invalid workflow YAML; refusing dispatch") from None
    if not isinstance(doc, dict):
        raise GitHubActionsError("Workflow YAML root must be a mapping")
    # PyYAML YAML 1.1 treats `on` as boolean True. Support both spellings.
    triggers = doc.get("on", doc.get(True))
    if isinstance(triggers, dict):
        if "workflow_dispatch" not in triggers:
            raise GitHubActionsError("Selected workflow does not declare workflow_dispatch")
        trigger = triggers["workflow_dispatch"]
    elif triggers == "workflow_dispatch" or (isinstance(triggers, list) and "workflow_dispatch" in triggers):
        trigger = None
    else:
        raise GitHubActionsError("Selected workflow does not declare workflow_dispatch")
    if trigger is None:
        return {}
    if not isinstance(trigger, dict):
        raise GitHubActionsError("Invalid workflow_dispatch definition")
    definitions = trigger.get("inputs", {}) or {}
    if not isinstance(definitions, dict) or len(definitions) > 25:
        raise GitHubActionsError("Invalid workflow_dispatch inputs definition")
    output: dict[str, Any] = {}
    for key, entry in definitions.items():
        if not isinstance(key, str) or not _NAME.fullmatch(key) or not isinstance(entry, dict):
            raise GitHubActionsError("Invalid workflow_dispatch input metadata")
        kind = entry.get("type", "string")
        if kind not in ("string", "choice", "boolean", "number", "environment"):
            raise GitHubActionsError("Unsupported workflow_dispatch input type")
        options = entry.get("options", [])
        if kind == "choice" and (not isinstance(options, list) or not options or
                                  any(not isinstance(o, str) for o in options)):
            raise GitHubActionsError("Invalid workflow_dispatch choice options")
        output[key] = {"type": kind, "required": entry.get("required") is True,
                       "has_default": "default" in entry, "options": options if kind == "choice" else []}
    return output


def _input_values(entries: list[str], json_file: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if json_file:
        p = Path(json_file).expanduser()
        try:
            if p.stat().st_size > _CONTENT_LIMIT:
                raise GitHubActionsError("Inputs JSON file exceeds limit")
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            raise GitHubActionsError("Cannot read valid inputs JSON file") from None
        if not isinstance(raw, dict):
            raise GitHubActionsError("Inputs JSON must be an object")
        for k, v in raw.items():
            if not isinstance(k, str) or not isinstance(v, (str, int, float, bool)) or v is None:
                raise GitHubActionsError("Inputs JSON keys/values must be scalar")
            if isinstance(v, bool):v = str(v).lower()
            result[k] = str(v)
    for pair in entries:
        if "=" not in pair:
            raise GitHubActionsError("Each --input must have KEY=VALUE syntax")
        k, v = pair.split("=", 1)
        if k in result:
            raise GitHubActionsError("Duplicate workflow input key")
        result[k] = v
    if len(result) > 25 or any(not _NAME.fullmatch(k) or len(v) > 65535 for k, v in result.items()):
        raise GitHubActionsError("Input keys or values exceed GitHub Actions limits")
    return result


def _validated_inputs(definitions: dict[str, Any], values: dict[str, str]) -> dict[str, str]:
    for key in values:
        if key not in definitions:
            raise GitHubActionsError("Provided input is not declared by selected workflow")
        meta = definitions[key]
        value = values[key]
        if meta["type"] == "choice" and value not in meta["options"]:
            raise GitHubActionsError("Input does not match declared workflow choices")
        if meta["type"] == "boolean" and value not in ("true", "false"):
            raise GitHubActionsError("Boolean workflow input must be true or false")
        if meta["type"] == "number":
            try:
                from decimal import Decimal
                if not Decimal(value).is_finite():raise ValueError()
            except (ValueError, ArithmeticError):
                raise GitHubActionsError("Numeric workflow input is invalid") from None
    for key, meta in definitions.items():
        if meta["required"] and not meta["has_default"] and key not in values:
            raise GitHubActionsError("Required workflow input was not provided")
    return values


def _state_root() -> Path:
    home = Path(os.environ.get("XDG_STATE_HOME") or str(Path.home()/".local/state"))
    return home / "devtool" / "actions" / "dispatches"


def _durable_record(path: Path, obj: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".dispatch-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as fp:
            os.fchmod(fp.fileno(),0o600)
            json.dump(obj,fp,sort_keys=True,indent=2)
            fp.write("\n")
            fp.flush();os.fsync(fp.fileno())
        os.replace(name,path)
        dfd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        if os.path.exists(name):os.unlink(name)


def dispatch_workflow(client: GitHubActionsClient, slug: str, workflow_id: str, ref: str,
                      inputs: dict[str,str], *, execute: bool, state_root: Path | None=None) -> dict[str, Any]:
    slug = _slug(slug)
    ref = _ref(ref)
    if execute and not client._token:
        raise GitHubActionsError("Dispatch requires authenticated GitHub Actions write permission")
    repo = client.repository(slug)
    workflow = client.workflow(slug,workflow_id)
    if workflow.get("state") != "active":
        raise GitHubActionsError("Selected workflow is not active")
    path = _workflow_path(workflow.get("path"))
    # Resolve ref into its current commit. This is an observation, not a lock:
    # the branch or tag may be changed by an external actor before dispatch.
    commit = client._get(f"/repos/{slug}/commits/{quote(ref,safe='')}")
    sha = commit.get("sha")
    if not isinstance(sha,str) or not _SHA.fullmatch(sha):
        raise GitHubActionsError("Could not resolve selected ref to an exact commit")
    blob = client._get(f"/repos/{slug}/contents/{path}?ref={quote(sha,safe='')}")
    if blob.get("type") != "file" or blob.get("path") != path or blob.get("encoding") != "base64":
        raise GitHubActionsError("Workflow source at selected commit is unavailable")
    try:
        text=base64.b64decode(blob["content"],validate=False)
    except (TypeError, ValueError):
        raise GitHubActionsError("Invalid workflow source encoding") from None
    defs=_parse_workflow_yaml(text)
    _validated_inputs(defs,inputs)
    summary={"repository":repo["full_name"],"repository_id":repo["id"],"workflow_id":workflow["id"],
             "workflow_path":path,"ref":ref,"pre_dispatch_sha":sha,"input_names":sorted(inputs),
             "source_binding":"observed-before-dispatch; mutable refs are not locked",
             "permission_preflight": {
                 "authenticated": bool(client._token),
                 "repository_accessible": True,
                 "repository_push_hint": repo.get("permissions", {}).get("push"),
                 "actions_write_permission": "not-independently-verifiable",
                 "authoritative_check": "GitHub dispatch POST (only with --yes)"}}
    if not execute:
        return {"phase":"ACTIONS-02","state":"validated-plan","dispatched":False,**summary}
    identity=uuid.uuid4().hex
    location=(state_root if state_root is not None else _state_root())/(identity+".json")
    obj={"schema":1,"request_id":identity,"created_at":datetime.now(timezone.utc).isoformat(),
         "state":"dispatch-outcome-unknown","run_id":None,**summary}
    _durable_record(location,obj)  # Must succeed before sending non-idempotent POST.
    try:
        result=client.dispatch(slug, str(workflow["id"]), ref, inputs)
    except GitHubDispatchOutcomeUnknown:
        # Successful POST with malformed response and transport outages are uncertain.
        raise GitHubActionsError(f"Dispatch outcome uncertain; do not retry blindly (request {identity})") from None
    except GitHubActionsError:
        obj["state"] = "rejected-by-api"
        _durable_record(location, obj)
        raise
    obj["state"]=result["state"]
    obj["run_id"]=result.get("run_id")
    obj["run_url"]=result.get("run_url")
    try:
        _durable_record(location,obj)
    except OSError:
        raise GitHubActionsError(f"Dispatch response received but journal update failed; inspect request {identity} before retrying") from None
    return {"phase":"ACTIONS-02","dispatched":True,"request_id":identity,
            "state":result["state"],"run_id":result.get("run_id"),"run_url":result.get("run_url"),
            "execution_complete":False,**summary}


def inspect_uncorrelated_dispatch(client: GitHubActionsClient, request_id: str, *,
                                  state_root: Path | None = None) -> dict[str, Any]:
    """Inspect bounded possible GitHub runs for HTTP 204; never associate one.

    Run identity cannot be inferred from workflow, ref, SHA and time alone:
    identical manual/concurrent dispatches can all satisfy those predicates.
    An operator must inspect the selected remote run separately by --run-id.
    """
    from .actions_runs import _read_dispatch_receipt, _normalize_run
    _, receipt = _read_dispatch_receipt(request_id, state_root)
    if receipt.get("run_id") is not None:
        raise GitHubActionsError("Dispatch is already correlated; use actions resume --request-id")
    if receipt.get("state") not in ("accepted-uncorrelated", "dispatch-outcome-unknown"):
        raise GitHubActionsError("Receipt does not represent an unresolved dispatch")
    slug = _slug(receipt["repository"])
    try:
        created = datetime.fromisoformat(receipt["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise GitHubActionsError("Dispatch receipt has invalid creation time") from None
    if not _SHA.fullmatch(str(receipt.get("pre_dispatch_sha", ""))):
        raise GitHubActionsError("Dispatch receipt lacks pre-dispatch commit")
    if type(receipt.get("workflow_id")) is not int or receipt["workflow_id"] <= 0:
        raise GitHubActionsError("Dispatch receipt lacks workflow identity")
    ref = _ref(str(receipt.get("ref", "")))
    candidates = []
    # Only the first 100 most recent runs. This is a bounded *hint*, not a proof.
    for raw in client.runs(slug, limit=100):
        try:
            created_at = datetime.fromisoformat(str(raw.get("created_at") or "").replace("Z", "+00:00"))
            if created_at.tzinfo is None:continue
        except ValueError:
            continue
        age = (created_at - created).total_seconds()
        if not (-60 <= age <= 1200):continue
        if (raw.get("event") != "workflow_dispatch" or
            raw.get("workflow_id") != receipt["workflow_id"] or
            str(raw.get("head_branch") or "") != ref or
            str(raw.get("head_sha") or "").lower() != receipt["pre_dispatch_sha"].lower()):
            continue
        try: run = _normalize_run(raw, slug)
        except GitHubActionsError: continue
        candidates.append({"run_id":run["id"], "run_attempt":run["run_attempt"],
                           "head_sha":run["head_sha"], "created_at":run["created_at"],
                           "html_url":run["html_url"], "conclusion":run["conclusion"]})
    return {"phase":"ACTIONS-G1", "operation":"dispatch-candidate-inspection",
            "request_id":request_id, "repository":slug,
            "dispatch_state":receipt["state"], "run_id":None,
            "candidate_count":len(candidates), "candidates":candidates,
            "scan":"bounded-most-recent-100; not exhaustive",
            "correlated":False, "automatically_reconciled":False,
            "safe_to_redispatch":False, "source_attested":False,
            "qualified_release":False,
            "advice":"Candidates are not proof of which run arose from HTTP 204. Inspect an explicit --run-id; do not blindly re-dispatch."}
