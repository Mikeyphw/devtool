from __future__ import annotations

"""OCI-05 repository execution preparation inside the caller's EXO run."""

from dataclasses import dataclass
import shlex
from typing import Any, Mapping

from .core.execution_contract import ExecutionStatus
from .progress_events import RunnerExecutionAdapter
from .oci_image import canonical_android_oci_definition
from .oci_workspace import OciWorkspacePlan, prepare_oci_workspace_for_execution
from .ssh_tmux_runner import SSHRunnerConfig, build_ssh_remote_sh_argv
from . import proot_oci as _runtime


@dataclass(frozen=True, slots=True)
class OciExecutionState:
    plan: OciWorkspacePlan
    image_reference: str
    image_digest: str
    archive_sha256: str
    archive_size: int
    container: str
    architecture: str
    definition_digest: str
    build_context_sha256: str
    validation_node_id: str = ""
    dry_run: bool = False

    def metadata(self) -> dict[str, Any]:
        return {
            "execution_provider": "ssh-proot-oci",
            "oci_runtime": "proot-distro-oci",
            "oci_container": self.container,
            "oci_image_reference": self.image_reference,
            "oci_image_digest": self.image_digest,
            "oci_image_archive_sha256": self.archive_sha256,
            "oci_image_archive_size": self.archive_size,
            "oci_architecture": self.architecture,
            "oci_definition_digest": self.definition_digest,
            "oci_build_context_sha256": self.build_context_sha256,
            "oci_guest_workspace": self.plan.working_directory,
            "oci_guest_cache": "/var/cache/devtool",
            "oci_source_sync_id": self.plan.source_sync_id,
            "oci_dry_run": self.dry_run,
        }


def _semantic_failure(
    config: SSHRunnerConfig,
    *,
    run_id: str,
    action: str,
    message: str,
    failure_kind: str,
    diagnostic_code: str,
    depends_on: tuple[str, ...] = (),
) -> None:
    """Record a semantic OCI-05 failure in canonical EXO truth before raising."""
    safe = _runtime._safe_text(message)
    node = _runtime._stable_node_id(config, action, safe[:160])
    _runtime._discover_nodes(run_id, ((node, "Validate OCI repository execution evidence", depends_on),))
    _runtime._start_node(run_id, node, "Validate OCI repository execution evidence", safe)
    adapter = RunnerExecutionAdapter(
        run_id=run_id,
        parent_node_id=node,
        tool="oci-execution",
        source="oci-execution",
        provenance=_runtime._provenance(config, operation="OCI-05 semantic validation"),
    )
    diagnostic_id = adapter.diagnostic(
        severity="error",
        code=diagnostic_code,
        message=safe,
        failure_kind=failure_kind,
        evidence=(safe[-2048:],),
    )
    _runtime._finish_node(
        run_id, node, status=ExecutionStatus.FAILED.value, exit_code=65,
        diagnostic_ids=(diagnostic_id,), reason=safe,
    )
    raise ValueError(safe)


def _inspect_script(*, container: str, context_sha256: str) -> str:
    container_q = shlex.quote(container)
    context_q = shlex.quote(context_sha256)
    return f'''set -eu
nested=0
if [ -n "${{PROOT_TMP_DIR:-}}" ] || [ -n "${{PROOT_LOADER:-}}" ]; then nested=1; fi
pd_bin="$(command -v proot-distro 2>/dev/null || command -v pd 2>/dev/null || true)"
proot_bin="${{PD_PROOT_BIN:-}}"; if [ -z "$proot_bin" ] || [ ! -x "$proot_bin" ]; then proot_bin="$(command -v proot 2>/dev/null || true)"; fi
runtime_exists=0
if [ -n "$pd_bin" ] && [ -n "$proot_bin" ] && [ "$nested" -eq 0 ]; then runtime_exists=1; fi
container_exists=0
installed_definition_digest=""
installed_context_sha256=""
embedded_runtime_ok=0
if [ "$runtime_exists" -eq 1 ]; then
  if "$pd_bin" list --quiet 2>/dev/null | grep -Fx -- {container_q} >/dev/null 2>&1; then
    container_exists=1
    probe="$($pd_bin login {container_q} -- /bin/bash -lc 'python3 - <<'"'"'PYMETA'"'"'
import hashlib,json,os
p="/usr/local/lib/devtool/oci-build-metadata.json"
try:
    d=json.load(open(p,encoding="utf-8"))
except Exception:
    d={{}}
print("installed_definition_digest="+str(d.get("definition_digest") or ""))
print("installed_context_sha256="+str(d.get("context_sha256") or ""))
expected={{
"/usr/local/lib/devtool/build-apk":"c35521e80c7f05b734e23c444922fa7b02820d9318c17494d9f0076fc23f70fc",
"/usr/local/lib/devtool/termux_toolchains.py":"46bff66338eee52d297b90cbfe6773e979d9a27d0b3c93f7816a139cf0e255fe",
"/usr/local/lib/devtool/devtool-events.init.gradle":"da7550a61bae530a8bb407c64574a456f77398db29332470623b47028bd9a50f",
}}
ok=all(os.path.isfile(path) and hashlib.sha256(open(path,"rb").read()).hexdigest()==digest for path,digest in expected.items())
print("embedded_runtime_ok="+("1" if ok else "0"))
PYMETA' 2>/dev/null || true)"
    printf '%s\n' "$probe"
  fi
fi
receipt="$HOME/.cache/devtool/oci/install-receipts/{container}.json"
installed_image_reference=""
installed_image_digest=""
installed_archive_sha256=""
installed_archive_size=0
if [ -f "$receipt" ]; then
  receipt_probe="$(python3 - "$receipt" <<'PYRECEIPT'
import json,sys
try:
    d=json.load(open(sys.argv[1],encoding="utf-8"))
except Exception:
    d={{}}
print("installed_image_reference="+str(d.get("image_reference") or ""))
print("installed_image_digest="+str(d.get("image_digest") or ""))
print("installed_archive_sha256="+str(d.get("archive_sha256") or ""))
print("installed_archive_size="+str(int(d.get("archive_size") or 0)))
PYRECEIPT
)"
  printf '%s\n' "$receipt_probe"
fi
archive="$HOME/.cache/devtool/oci/builds/android-arm64-{context_sha256}.oci.tar"
archive_exists=0
image_digest=""
archive_sha256=""
archive_size=0
if [ -s "$archive" ]; then
  archive_exists=1
  archive_sha256="$(sha256sum "$archive" | awk '{{print $1}}')"
  archive_size="$(wc -c < "$archive" | tr -d ' ')"
  image_digest="$(python3 - "$archive" <<'PYDIGEST'
import hashlib,json,sys,tarfile
p=sys.argv[1]
try:
    with tarfile.open(p,"r:*") as t:
        idx=json.load(t.extractfile("index.json")); manifests=idx.get("manifests") or []
        if len(manifests)!=1: raise ValueError
        digest=str(manifests[0].get("digest") or "")
        algo,hexv=digest.split(":",1)
        if algo!="sha256" or len(hexv)!=64: raise ValueError
        blob=t.extractfile("blobs/sha256/"+hexv).read()
        if hashlib.sha256(blob).hexdigest()!=hexv: raise ValueError
        print(digest)
except Exception:
    print("")
PYDIGEST
)"
fi
printf 'runtime_exists=%s\nnested_proot=%s\ncontainer_exists=%s\narchive_exists=%s\nimage_digest=%s\narchive_sha256=%s\narchive_size=%s\n' \
  "$runtime_exists" "$nested" "$container_exists" "$archive_exists" "$image_digest" "$archive_sha256" "$archive_size"
'''


def _inspect_state(
    config: SSHRunnerConfig,
    *,
    run_id: str,
    container: str,
    context_sha256: str,
    action: str,
    depends_on: tuple[str, ...],
) -> tuple[dict[str, str], str]:
    node = _runtime._stable_node_id(config, action, f"{container}|{context_sha256}")
    _runtime._discover_nodes(run_id, ((node, "Inspect OCI repository execution state", depends_on),))
    completed = _runtime._run_process(
        config, run_id=run_id, node_id=node, label="Inspect OCI repository execution state",
        argv=build_ssh_remote_sh_argv(config, _inspect_script(container=container, context_sha256=context_sha256), tty=False),
        detail="Inspecting PRoot-Distro runtime, installed container identity, and canonical image archive evidence",
        timeout=max(_runtime._transport_timeout(), 20.0), diagnostic_code="OCI05_EXECUTION_INSPECT",
        provenance_extra={"container": container, "context_sha256": context_sha256},
        required_output_keys=("runtime_exists", "nested_proot", "container_exists", "archive_exists", "archive_size"),
        required_output_patterns={
            "runtime_exists": r"[01]", "nested_proot": r"[01]", "container_exists": r"[01]",
            "archive_exists": r"[01]", "archive_size": r"[0-9]+",
        },
    )
    return _runtime._parse_key_values(completed.stdout), node


def _install_built_image(
    config: SSHRunnerConfig,
    *,
    run_id: str,
    container: str,
    definition_digest: str,
    context_sha256: str,
    depends_on: tuple[str, ...],
) -> str:
    node = _runtime._stable_node_id(config, "oci05-install-built-image", f"{context_sha256}|{container}")
    _runtime._discover_nodes(run_id, ((node, f"Install canonical OCI image as {container}", depends_on),))
    _runtime._run_process(
        config, run_id=run_id, node_id=node, label=f"Install canonical OCI image as {container}",
        argv=build_ssh_remote_sh_argv(
            config,
            _runtime._oci_install_built_script(
                definition_digest=definition_digest, context_sha256=context_sha256, name=container
            ),
            tty=False,
        ),
        detail="Installing the exact canonical content-addressed OCI archive",
        timeout=_runtime._oci_timeout(), diagnostic_code="OCI05_IMAGE_INSTALL",
        provenance_extra={"container": container, "definition_digest": definition_digest, "context_sha256": context_sha256},
    )
    return node


def _install_receipt_script(
    *,
    container: str,
    image_reference: str,
    image_digest: str,
    archive_sha256: str,
    archive_size: int,
    definition_digest: str,
    context_sha256: str,
) -> str:
    values = [
        container,
        image_reference,
        image_digest,
        archive_sha256,
        str(int(archive_size)),
        definition_digest,
        context_sha256,
    ]
    quoted = " ".join(shlex.quote(value) for value in values)
    return f'''set -eu
root="$HOME/.cache/devtool/oci/install-receipts"
umask 077
mkdir -p "$root"
python3 - "$root" {quoted} <<'PYRECEIPT'
import json,os,sys,tempfile
root,container,image_reference,image_digest,archive_sha256,archive_size,definition_digest,context_sha256=sys.argv[1:]
payload={{
    "schema_version":1,
    "kind":"devtool.oci05.install-receipt",
    "container":container,
    "image_reference":image_reference,
    "image_digest":image_digest,
    "archive_sha256":archive_sha256,
    "archive_size":int(archive_size),
    "definition_digest":definition_digest,
    "context_sha256":context_sha256,
}}
path=os.path.join(root,container+".json")
fd,tmp=tempfile.mkstemp(prefix="."+container+".",dir=root)
try:
    os.fchmod(fd,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as h:
        json.dump(payload,h,sort_keys=True,separators=(",",":"))
        h.write("\n")
        h.flush()
        os.fsync(h.fileno())
    os.replace(tmp,path)
finally:
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
print("install_receipt="+path)
PYRECEIPT
'''


def _write_install_receipt(
    config: SSHRunnerConfig,
    *,
    run_id: str,
    image_reference: str,
    image_digest: str,
    archive_sha256: str,
    archive_size: int,
    definition_digest: str,
    context_sha256: str,
    depends_on: tuple[str, ...],
) -> str:
    node = _runtime._stable_node_id(
        config,
        "oci05-install-receipt",
        f"{config.oci_container}|{image_digest}|{archive_sha256}",
    )
    _runtime._discover_nodes(run_id, ((node, "Record installed OCI image provenance", depends_on),))
    _runtime._run_process(
        config,
        run_id=run_id,
        node_id=node,
        label="Record installed OCI image provenance",
        argv=build_ssh_remote_sh_argv(
            config,
            _install_receipt_script(
                container=config.oci_container,
                image_reference=image_reference,
                image_digest=image_digest,
                archive_sha256=archive_sha256,
                archive_size=archive_size,
                definition_digest=definition_digest,
                context_sha256=context_sha256,
            ),
            tty=False,
        ),
        detail="Binding installed container identity to the exact verified OCI archive evidence",
        timeout=max(_runtime._transport_timeout(), 20.0),
        diagnostic_code="OCI05_INSTALL_RECEIPT",
        provenance_extra={
            "container": config.oci_container,
            "image_reference": image_reference,
            "image_digest": image_digest,
            "image_sha256": archive_sha256,
            "definition_digest": definition_digest,
            "context_sha256": context_sha256,
        },
        required_output_keys=("install_receipt",),
        required_output_patterns={"install_receipt": r"/.+"},
    )
    return node


def prepare_oci_execution(
    config: SSHRunnerConfig,
    *,
    repo_root: str,
    run_id: str,
    workspace_id: str,
    remote_root: str,
    source_sync_id: str,
    source_revision: Mapping[str, Any] | None,
    dry_run: bool = False,
    environment: Mapping[str, str] | None = None,
    workspace_planned: bool = False,
) -> OciExecutionState:
    """Prepare canonical OCI repository execution without creating a second run."""
    definition = canonical_android_oci_definition()
    if config.oci_architecture != "aarch64" or definition.architecture != "aarch64":
        raise ValueError("OCI repository execution requires the canonical aarch64 Android image contract")
    if not str(config.oci_image or "").strip():
        raise ValueError("OCI repository execution requires a non-empty image reference")
    if not str(config.oci_container or "").strip():
        raise ValueError("OCI repository execution requires a container name")
    _runtime._validate_name(config.oci_container)

    plan, workspace_node = prepare_oci_workspace_for_execution(
        config, run_id=run_id, workspace_id=workspace_id, remote_root=remote_root,
        source_sync_id=source_sync_id, source_revision=source_revision,
        dry_run=dry_run, environment=environment, workspace_planned=workspace_planned,
    )
    values, inspect_node = _inspect_state(
        config, run_id=run_id, container=config.oci_container,
        context_sha256=definition.context_sha256, action="oci05-runtime-image-inspect",
        depends_on=(workspace_node,),
    )
    if values.get("nested_proot") == "1":
        _semantic_failure(
            config, run_id=run_id, action="oci05-nested-proot-refusal",
            message="OCI repository execution refuses a worker that is already inside PRoot",
            failure_kind="nested_runtime_error", diagnostic_code="OCI05_NESTED_PROOT",
            depends_on=(inspect_node,),
        )

    runtime_exists = values.get("runtime_exists") == "1"
    current_node = inspect_node
    if not runtime_exists:
        if dry_run:
            return OciExecutionState(
                plan=plan, image_reference=config.oci_image, image_digest=str(values.get("image_digest") or ""),
                archive_sha256=str(values.get("archive_sha256") or ""), archive_size=int(values.get("archive_size") or 0),
                container=config.oci_container, architecture=config.oci_architecture,
                definition_digest=definition.definition_digest, build_context_sha256=definition.context_sha256,
                dry_run=True,
            )
        if config.bootstrap_policy == "never":
            _semantic_failure(
                config, run_id=run_id, action="oci05-runtime-policy-refusal",
                message="PRoot-Distro runtime is missing and bootstrap policy is 'never'",
                failure_kind="runtime_missing", diagnostic_code="OCI05_RUNTIME_MISSING",
                depends_on=(inspect_node,),
            )
        bootstrap = _runtime.oci_bootstrap_payload(config, run_id=run_id, depends_on=(inspect_node,))
        current_node = str((bootstrap.get("node_ids") or [inspect_node])[-1])
        values, current_node = _inspect_state(
            config, run_id=run_id, container=config.oci_container,
            context_sha256=definition.context_sha256, action="oci05-runtime-after-bootstrap",
            depends_on=(current_node,),
        )
        if values.get("runtime_exists") != "1":
            _semantic_failure(
                config, run_id=run_id, action="oci05-runtime-after-bootstrap-failure",
                message="PRoot-Distro runtime is still unavailable after bootstrap",
                failure_kind="runtime_missing", diagnostic_code="OCI05_RUNTIME_MISSING",
                depends_on=(current_node,),
            )

    if dry_run:
        return OciExecutionState(
            plan=plan, image_reference=config.oci_image, image_digest=str(values.get("image_digest") or ""),
            archive_sha256=str(values.get("archive_sha256") or ""), archive_size=int(values.get("archive_size") or 0),
            container=config.oci_container, architecture=config.oci_architecture,
            definition_digest=definition.definition_digest, build_context_sha256=definition.context_sha256,
            dry_run=True,
        )

    image_digest = str(values.get("image_digest") or "")
    archive_sha256 = str(values.get("archive_sha256") or "")
    archive_size = int(values.get("archive_size") or 0)
    image_ready_node = current_node
    if values.get("archive_exists") != "1" or not archive_sha256 or not image_digest:
        built = _runtime.oci_build_payload(
            config, run_id=run_id, tag=config.oci_image, dry_run=False,
            depends_on=(current_node,),
        )
        built_digest = str(built.get("image_digest") or "")
        built_sha256 = str(built.get("image_sha256") or "")
        built_size = int(built.get("image_size") or 0)
        build_node = str((built.get("node_ids") or [current_node])[-1])
        values, image_ready_node = _inspect_state(
            config, run_id=run_id, container=config.oci_container,
            context_sha256=definition.context_sha256, action="oci05-image-after-build",
            depends_on=(build_node,),
        )
        image_digest = str(values.get("image_digest") or "")
        archive_sha256 = str(values.get("archive_sha256") or "")
        archive_size = int(values.get("archive_size") or 0)
        if (
            values.get("archive_exists") != "1"
            or not image_digest
            or not archive_sha256
            or image_digest != built_digest
            or archive_sha256 != built_sha256
            or archive_size != built_size
        ):
            _semantic_failure(
                config, run_id=run_id, action="oci05-built-image-evidence-mismatch",
                message="OCI image evidence changed or disappeared between deterministic build and re-inspection",
                failure_kind="image_evidence_mismatch", diagnostic_code="OCI05_IMAGE_EVIDENCE",
                depends_on=(image_ready_node,),
            )
    if config.oci_digest_policy == "require" and not image_digest:
        _semantic_failure(
            config, run_id=run_id, action="oci05-digest-policy-refusal",
            message="OCI digest policy 'require' needs verified OCI manifest digest evidence",
            failure_kind="image_digest_missing", diagnostic_code="OCI05_IMAGE_DIGEST",
            depends_on=(image_ready_node,),
        )

    container_exists = values.get("container_exists") == "1"
    installed_definition = str(values.get("installed_definition_digest") or "")
    installed_context = str(values.get("installed_context_sha256") or "")
    embedded_ok = values.get("embedded_runtime_ok") == "1"
    installed_image_reference = str(values.get("installed_image_reference") or "")
    installed_image_digest = str(values.get("installed_image_digest") or "")
    installed_archive_sha256 = str(values.get("installed_archive_sha256") or "")
    try:
        installed_archive_size = int(values.get("installed_archive_size") or 0)
    except (TypeError, ValueError):
        installed_archive_size = 0
    container_current = bool(
        container_exists
        and installed_definition == definition.definition_digest
        and installed_context == definition.context_sha256
        and embedded_ok
        and installed_image_reference == config.oci_image
        and installed_image_digest == image_digest
        and installed_archive_sha256 == archive_sha256
        and installed_archive_size == archive_size
    )

    install_dependency = image_ready_node
    if container_exists and not container_current:
        removed = _runtime.oci_remove_payload(
            config, config.oci_container, run_id=run_id, depends_on=(image_ready_node,)
        )
        install_dependency = str(removed.get("node_id") or image_ready_node)
        container_exists = False
    if not container_exists or not container_current:
        install_dependency = _install_built_image(
            config, run_id=run_id, container=config.oci_container,
            definition_digest=definition.definition_digest, context_sha256=definition.context_sha256,
            depends_on=(install_dependency,),
        )
        install_dependency = _write_install_receipt(
            config, run_id=run_id, image_reference=config.oci_image, image_digest=image_digest,
            archive_sha256=archive_sha256, archive_size=archive_size,
            definition_digest=definition.definition_digest, context_sha256=definition.context_sha256,
            depends_on=(install_dependency,),
        )

    validation = _runtime.oci_validate_image_payload(
        config, config.oci_container, run_id=run_id,
        depends_on=(install_dependency,), discover_graph=True,
    )
    validation_node = str(validation.get("node_id") or install_dependency)
    final_values, final_node = _inspect_state(
        config, run_id=run_id, container=config.oci_container,
        context_sha256=definition.context_sha256, action="oci05-final-evidence-inspect",
        depends_on=(validation_node,),
    )
    if final_values.get("container_exists") != "1" or final_values.get("embedded_runtime_ok") != "1":
        _semantic_failure(
            config, run_id=run_id, action="oci05-final-container-capability-failure",
            message="OCI final evidence inspection did not confirm the canonical embedded-runtime container",
            failure_kind="image_capability_error", diagnostic_code="OCI05_FINAL_IMAGE", depends_on=(final_node,),
        )
    if str(final_values.get("installed_definition_digest") or "") != definition.definition_digest:
        _semantic_failure(
            config, run_id=run_id, action="oci05-final-definition-mismatch",
            message="OCI final container definition digest does not match the canonical image definition",
            failure_kind="image_definition_mismatch", diagnostic_code="OCI05_FINAL_IMAGE", depends_on=(final_node,),
        )
    if str(final_values.get("installed_context_sha256") or "") != definition.context_sha256:
        _semantic_failure(
            config, run_id=run_id, action="oci05-final-context-mismatch",
            message="OCI final container build context does not match the canonical image context",
            failure_kind="image_context_mismatch", diagnostic_code="OCI05_FINAL_IMAGE", depends_on=(final_node,),
        )
    try:
        final_installed_archive_size = int(final_values.get("installed_archive_size") or 0)
    except (TypeError, ValueError):
        final_installed_archive_size = 0
    if (
        str(final_values.get("installed_image_reference") or "") != config.oci_image
        or str(final_values.get("installed_image_digest") or "") != image_digest
        or str(final_values.get("installed_archive_sha256") or "") != archive_sha256
        or final_installed_archive_size != archive_size
    ):
        _semantic_failure(
            config, run_id=run_id, action="oci05-final-install-provenance-mismatch",
            message="OCI final installed-container receipt does not match the exact verified image archive evidence",
            failure_kind="image_install_provenance_mismatch", diagnostic_code="OCI05_FINAL_IMAGE", depends_on=(final_node,),
        )
    image_digest = str(final_values.get("image_digest") or image_digest)
    archive_sha256 = str(final_values.get("archive_sha256") or archive_sha256)
    archive_size = int(final_values.get("archive_size") or archive_size or 0)
    if config.oci_digest_policy == "require" and not image_digest:
        _semantic_failure(
            config, run_id=run_id, action="oci05-final-digest-missing",
            message="OCI final evidence is missing the required image manifest digest",
            failure_kind="image_digest_missing", diagnostic_code="OCI05_IMAGE_DIGEST", depends_on=(final_node,),
        )

    return OciExecutionState(
        plan=plan, image_reference=config.oci_image, image_digest=image_digest,
        archive_sha256=archive_sha256, archive_size=archive_size,
        container=config.oci_container, architecture=config.oci_architecture,
        definition_digest=definition.definition_digest, build_context_sha256=definition.context_sha256,
        validation_node_id=final_node, dry_run=False,
    )
