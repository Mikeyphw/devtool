from __future__ import annotations

"""Authoritative reducer/store for typed Devtool execution events."""

from collections.abc import Iterable
from copy import deepcopy
from threading import RLock
from typing import TYPE_CHECKING, Any

from .execution_contract import ExecutionStatus, NodeId, RunId, canonical_execution_status
from .execution_semantics import ExecutionIntent, NodeDefinitionIdentity, ReusableNodeRecord, ReuseDisposition, shadow_reuse_metadata
from .reuse import DependencyFingerprint, aggregate_fingerprints, aggregate_output_manifests, stable_digest, result_identity
from .execution_limits import (
    MAX_PROCESS_LAST_OUTPUT_CHARS,
    bounded_log_ref,
    merge_bounded_evidence,
)
from .execution_state import (
    ExecutionAccountingScope,
    ExecutionArtifactState,
    ExecutionDiagnosticState,
    ExecutionNodeState,
    ExecutionProcessState,
    ExecutionRunState,
    ExecutionSnapshot,
    execution_accounting_scope,
)
if TYPE_CHECKING:
    from ..workflow_graph import ExecutionGraph
    from .events.events import DevtoolEvent


class ExecutionStoreError(RuntimeError):
    pass


class ExecutionSequenceError(ExecutionStoreError):
    pass


class ExecutionTransitionError(ExecutionStoreError):
    pass


class ExecutionReducer:
    """Apply a typed event to one mutable run state.

    The reducer deliberately does not synthesize success for unobserved work.
    A terminal run and its child-node accounting are separate facts; later UI
    surfaces can therefore expose unresolved/planned nodes rather than turning
    them into false positives.
    """

    def apply(self, run: ExecutionRunState, event: "DevtoolEvent") -> None:
        from .events.events import (
            ArtifactProduced,
            DiagnosticEmitted,
            GraphSealed,
            MetricChanged,
            NodeBlocked,
            NodeDiscovered,
            NodeFinished,
            NodeProgressChanged,
            NodeStarted,
            ProcessExited,
            ProcessOutputReceived,
            ProcessStarted,
            RunFinished,
            RunStarted,
        )
        if event.event_id in run.applied_event_ids:
            return
        if event.sequence < 0:
            raise ExecutionSequenceError("execution events must have a completed non-negative sequence")
        if run.last_sequence >= 0 and event.sequence <= run.last_sequence:
            raise ExecutionSequenceError(
                f"execution event sequence regression for run {run.run_id!r}: "
                f"{event.sequence} <= {run.last_sequence}"
            )

        if isinstance(event, RunStarted):
            self._run_started(run, event)
        elif isinstance(event, RunFinished):
            self._run_finished(run, event)
        elif isinstance(event, GraphSealed):
            self._graph_sealed(run, event)
        elif isinstance(event, NodeDiscovered):
            self._node_discovered(run, event)
        elif isinstance(event, NodeStarted):
            self._node_started(run, event)
        elif isinstance(event, NodeProgressChanged):
            self._node_progress(run, event)
        elif isinstance(event, NodeBlocked):
            self._node_blocked(run, event)
        elif isinstance(event, NodeFinished):
            self._node_finished(run, event)
        elif isinstance(event, ProcessStarted):
            self._process_started(run, event)
        elif isinstance(event, ProcessOutputReceived):
            self._process_output(run, event)
        elif isinstance(event, ProcessExited):
            self._process_exited(run, event)
        elif isinstance(event, DiagnosticEmitted):
            self._diagnostic(run, event)
        elif isinstance(event, ArtifactProduced):
            self._artifact(run, event)
        elif isinstance(event, MetricChanged):
            self._metric(run, event)

        run.last_sequence = int(event.sequence)
        run.last_event_id = str(event.event_id)
        run.applied_event_ids.add(str(event.event_id))

    def plan_graph(self, run: ExecutionRunState, graph: "ExecutionGraph") -> None:
        """Seed runtime nodes from the existing workflow DAG without cloning it.

        Node ids are the workflow graph keys verbatim. Dependency edges are
        copied only as mutable runtime state so status/progress can evolve.
        """
        if run.graph_sealed:
            incoming = {str(NodeId.workflow(node.key)) for node in graph.nodes}
            unknown = sorted(incoming.difference(run.nodes))
            if unknown:
                raise ExecutionTransitionError(
                    f"execution graph {run.run_id!r} is sealed; cannot add planned node(s): {', '.join(unknown)}"
                )
        source = {
            "target": str(graph.target),
            "workflow": str(graph.workflow),
            "targets": [str(value) for value in graph.targets],
        }
        if source not in run.graph_sources:
            run.graph_sources.append(source)
        for workflow_node in graph.topological_order():
            node_id = str(NodeId.workflow(workflow_node.key))
            node = run.nodes.get(node_id)
            if node is None:
                node = ExecutionNodeState(node_id=node_id)
                run.nodes[node_id] = node
            node.label = node.label or str(workflow_node.label)
            node.kind = str(workflow_node.ref.kind or "operation")
            node.depends_on = tuple(str(value) for value in workflow_node.depends_on)
            definition = NodeDefinitionIdentity.from_workflow_node(workflow_node)
            intent = ExecutionIntent.from_workflow_node(workflow_node)
            node.metadata.update(
                {
                    "target": str(workflow_node.target),
                    "workflow": str(workflow_node.workflow),
                    "index": int(workflow_node.index),
                    "runner": str(workflow_node.runner),
                    "action": str(workflow_node.action),
                    "node_definition_id": definition.value,
                    "node_definition": definition.to_dict(),
                    "reuse_prediction": shadow_reuse_metadata(definition),
                    "execution_intent": intent.to_dict(),
                }
            )

    def _run_started(self, run: ExecutionRunState, event: "RunStarted") -> None:
        if run.status.terminal:
            raise ExecutionTransitionError(
                f"cannot start terminal run {run.run_id!r} with status {run.status.value!r}"
            )
        run.status = ExecutionStatus.RUNNING
        run.command = str(event.command or run.command)
        run.project = str(event.project or run.project)
        metadata = dict(event.metadata or {})
        invocation_source = str(metadata.get("invocation_source") or event.source or "").strip()
        invocation_command = str(metadata.get("invocation_command") or event.command or "").strip()
        if invocation_source:
            run.invocation_source = invocation_source
        elif not run.invocation_source:
            run.invocation_source = "devtool"
        if invocation_command:
            run.invocation_command = invocation_command
        elif not run.invocation_command:
            run.invocation_command = str(event.command or "")
        invocation_metadata = metadata.get("invocation_metadata")
        if isinstance(invocation_metadata, dict):
            run.invocation_metadata.update({str(key): value for key, value in invocation_metadata.items()})
        repository_identity = metadata.get("repository_identity")
        if isinstance(repository_identity, dict):
            run.repository_identity = {str(key): value for key, value in repository_identity.items()}
        run_lineage = metadata.get("run_lineage")
        if isinstance(run_lineage, dict):
            normalized_lineage = {str(key): value for key, value in run_lineage.items()}
            parent_run_id = str(normalized_lineage.get("parent_run_id") or "").strip()
            child_run_id = str(normalized_lineage.get("child_run_id") or run.run_id).strip()
            if parent_run_id and parent_run_id == run.run_id:
                raise ExecutionTransitionError("federated child run cannot name itself as parent_run_id")
            if child_run_id and child_run_id != run.run_id:
                raise ExecutionTransitionError(
                    f"run_lineage child_run_id {child_run_id!r} does not match event run_id {run.run_id!r}"
                )
            if normalized_lineage:
                normalized_lineage["child_run_id"] = run.run_id
                run.run_lineage = normalized_lineage
        for key in ("wrapper_schema", "wrapper_command"):
            value = metadata.get(key)
            if value not in (None, ""):
                run.invocation_metadata[str(key)] = value
        run.started_at = run.started_at or event.timestamp

    def _run_finished(self, run: ExecutionRunState, event: "RunFinished") -> None:
        status = canonical_execution_status(event.status)
        if not status.terminal:
            raise ExecutionTransitionError(f"run.finished requires terminal status, got {status.value!r}")
        if run.status.terminal and run.status is not status:
            raise ExecutionTransitionError(
                f"run {run.run_id!r} already ended as {run.status.value!r}, cannot become {status.value!r}"
            )
        run.status = status
        run.exit_code = int(event.exit_code)
        run.duration_seconds = float(event.duration_seconds or 0.0)
        run.finished_at = event.timestamp

    def _graph_sealed(self, run: ExecutionRunState, event: "GraphSealed") -> None:
        if run.graph_sealed:
            # Replayed/duplicate sealing with the same truth is harmless.
            if run.graph_seal_reason and event.reason and run.graph_seal_reason != str(event.reason):
                raise ExecutionTransitionError(
                    f"execution graph {run.run_id!r} already sealed for {run.graph_seal_reason!r}"
                )
            return
        run.graph_sealed = True
        run.graph_sealed_at = event.timestamp
        run.graph_seal_reason = str(event.reason or "discovery complete")

    def _node_discovered(self, run: ExecutionRunState, event: "NodeDiscovered") -> None:
        node = self._ensure_event_node(run, event, label=event.label, kind=event.kind)
        if event.parent_id:
            node.parent_id = str(event.parent_id)
        if event.label:
            node.label = str(event.label)
        if event.kind:
            node.kind = str(event.kind)
        if event.depends_on:
            node.depends_on = tuple(str(value) for value in event.depends_on)
        node.discovered_at = node.discovered_at or event.timestamp
        node.metadata.update(dict(event.metadata or {}))

    def _node_started(self, run: ExecutionRunState, event: "NodeStarted") -> None:
        node = self._ensure_event_node(run, event, label=event.label, kind=event.kind)
        self._require_nonterminal(node, "start")
        node.status = ExecutionStatus.RUNNING
        node.transition_origin = "observed"
        node.transition_reason = ""
        if event.parent_id:
            node.parent_id = str(event.parent_id)
        if event.label:
            node.label = str(event.label)
        if event.kind:
            node.kind = str(event.kind)
        if event.argv:
            node.argv = tuple(str(value) for value in event.argv)
        node.discovered_at = node.discovered_at or event.timestamp
        node.started_at = node.started_at or event.timestamp
        incoming_metadata = dict(event.metadata or {})
        reuse_fp = incoming_metadata.get("reuse_fingerprint")
        if isinstance(reuse_fp, dict):
            rows = list(node.metadata.get("reuse_spec_fingerprints") or [])
            digest = str(reuse_fp.get("digest") or "")
            if not rows or str(rows[-1].get("digest") or "") != digest:
                rows.append(dict(reuse_fp))
            node.metadata["reuse_spec_fingerprints"] = rows
        node.metadata.update(incoming_metadata)

    def _node_progress(self, run: ExecutionRunState, event: "NodeProgressChanged") -> None:
        node = self._ensure_event_node(run, event, kind="operation", allow_active_match=True)
        if not node.status.terminal:
            node.status = ExecutionStatus.RUNNING
            node.started_at = node.started_at or event.timestamp
        progress = node.progress
        progress.current = event.current
        progress.total = event.total
        progress.percent = event.percent
        progress.unit = str(event.unit or "")
        progress.indeterminate = bool(event.indeterminate)
        progress.detail = str(event.detail or "")

    def _node_blocked(self, run: ExecutionRunState, event: "NodeBlocked") -> None:
        node = self._ensure_event_node(run, event, allow_active_match=True)
        self._set_terminal_node_status(node, ExecutionStatus.BLOCKED)
        node.blocked_by = tuple(str(value) for value in event.blocked_by)
        node.blocked_reason = str(event.reason or "")
        node.transition_origin = "inferred"
        node.transition_reason = str(event.reason or "dependency blocked")
        node.finished_at = event.timestamp

    def _node_finished(self, run: ExecutionRunState, event: "NodeFinished") -> None:
        node = self._ensure_event_node(run, event, label=self._event_label(event), allow_active_match=True)
        status = canonical_execution_status(event.status)
        incoming_metadata = dict(event.metadata or {})
        incoming_output = incoming_metadata.get("reuse_output_manifest")
        if isinstance(incoming_output, dict):
            output_rows = list(node.metadata.get("reuse_spec_output_manifests") or [])
            digest = str(incoming_output.get("digest") or "")
            if not output_rows or str(output_rows[-1].get("digest") or "") != digest:
                output_rows.append(dict(incoming_output))
            node.metadata["reuse_spec_output_manifests"] = output_rows
        # Compatibility TaskFinished events can represent one command/spec of a
        # multi-spec workflow node. In that case the aggregate node correctly
        # remains RUNNING until the last part ends. EXO-05 will model the parts
        # as child operations, after which canonical NodeFinished can be
        # terminal-only again.
        if not status.terminal:
            self._require_nonterminal(node, "update")
            node.status = status
            node.exit_code = int(event.exit_code)
            node.duration_seconds += float(event.duration_seconds or 0.0)
            node.failure_kind = str(event.failure_kind or node.failure_kind or "")
            node.transition_origin = str(event.transition_origin or "observed")
            node.transition_reason = str(event.transition_reason or "")
            node.metadata.update(incoming_metadata)
            return
        self._set_terminal_node_status(node, status)
        node.exit_code = int(event.exit_code)
        node.duration_seconds += float(event.duration_seconds or 0.0)
        node.failure_kind = str(event.failure_kind or "")
        node.failure_process_id = str(event.process_id or node.failure_process_id or "")
        node.transition_origin = str(event.transition_origin or "observed")
        node.transition_reason = str(event.transition_reason or "")
        if event.diagnostic_ids:
            for diagnostic_id in event.diagnostic_ids:
                diagnostic_id = str(diagnostic_id)
                if diagnostic_id and diagnostic_id not in node.diagnostic_ids:
                    node.diagnostic_ids.append(diagnostic_id)
            if status is ExecutionStatus.FAILED and not node.primary_failure_diagnostic_id:
                node.primary_failure_diagnostic_id = str(event.diagnostic_ids[0])
        node.evidence_refs[:] = merge_bounded_evidence(node.evidence_refs, event.evidence)
        node.finished_at = event.timestamp
        node.metadata.update(incoming_metadata)
        definition_id = str(node.metadata.get("node_definition_id") or "").strip()
        if definition_id:
            spec_fingerprints = [dict(v) for v in node.metadata.get("reuse_spec_fingerprints", ()) or () if isinstance(v, dict)]
            latest_fp = node.metadata.get("reuse_fingerprint")
            if isinstance(latest_fp, dict) and not spec_fingerprints:
                spec_fingerprints = [dict(latest_fp)]
            aggregate = aggregate_fingerprints(definition_id, spec_fingerprints) if spec_fingerprints else {}
            # Final dependency identity comes from canonical upstream EXO terminal
            # history, not from a scheduler-local cache.
            dependency_rows: list[tuple[str, str]] = []
            dependency_complete = True
            for dependency_id in node.depends_on:
                dependency_node = run.nodes.get(str(dependency_id))
                dependency_record = (dependency_node.metadata.get("reusable_node_record") if dependency_node is not None else None)
                result_id = str(dependency_record.get("result_identity") or "") if isinstance(dependency_record, dict) else ""
                if not result_id:
                    dependency_complete = False
                    break
                dependency_rows.append((str(dependency_id), result_id))
            if aggregate and node.depends_on:
                terminal_dependency = DependencyFingerprint(tuple(dependency_rows), complete=dependency_complete)
                aggregate["dependency_digest"] = terminal_dependency.digest
                aggregate["dependency_complete"] = dependency_complete
                aggregate["digest"] = stable_digest({
                    key: aggregate.get(key, "") for key in (
                        "node_definition_id", "invocation_digest", "input_manifest_digest",
                        "dependency_digest", "environment_digest",
                    )
                })
            output_rows = [dict(v) for v in node.metadata.get("reuse_spec_output_manifests", ()) or () if isinstance(v, dict)]
            if not output_rows and isinstance(node.metadata.get("reuse_output_manifest"), dict):
                output_rows = [dict(node.metadata.get("reuse_output_manifest") or {})]
            output_manifest = aggregate_output_manifests(output_rows) if output_rows else {}
            evidence_refs = tuple(str(value) for value in node.evidence_refs if str(value))
            output_complete = bool(output_manifest.get("complete", False)) if output_manifest else False
            record_reusable = bool(aggregate and aggregate.get("reusable") and dependency_complete and output_complete and node.status.value == "succeeded")
            qualification_complete = bool(aggregate and output_manifest and dependency_complete and output_complete)
            result_id = str(node.metadata.get("reuse_result_identity") or "")
            if not result_id and aggregate and output_manifest:
                result_id = result_identity(
                    fingerprint_digest=str(aggregate.get("digest") or ""),
                    output_manifest_digest=str(output_manifest.get("digest") or ""),
                    execution_status=node.status.value,
                    evidence_refs=evidence_refs,
                )
            reason = "qualified-terminal-proof" if record_reusable and qualification_complete else "terminal-proof-incomplete-or-nonreusable"
            reusable_record = ReusableNodeRecord(
                node_definition_id=definition_id,
                source_run_id=str(run.run_id),
                source_node_id=str(node.node_id),
                execution_status=node.status.value,
                disposition=ReuseDisposition.RUN,
                reusable=record_reusable,
                qualification_complete=qualification_complete,
                evidence_refs=evidence_refs,
                reason=reason,
                fingerprint=aggregate,
                output_manifest=output_manifest,
                result_identity=result_id,
                portability_class=str(node.metadata.get("reuse_portability_class") or ""),
                spec_fingerprint_digests=tuple(str(v.get("digest") or "") for v in spec_fingerprints if str(v.get("digest") or "")),
            )
            # Attached to canonical EXO node history; any lookup index is derived.
            node.metadata["reusable_node_record"] = reusable_record.to_dict()


    def _process_started(self, run: ExecutionRunState, event: "ProcessStarted") -> None:
        process_id = str(event.process_id or "").strip()
        if not process_id:
            if event.pid is None:
                process_id = f"{run.run_id}:process:event:{event.sequence}"
            else:
                process_id = f"{run.run_id}:pid:{int(event.pid)}"
            event.process_id = process_id
        node_id = self._resolve_existing_node_id(run, event, allow_active_match=True)
        process = run.processes.get(process_id)
        if process is None:
            process = ExecutionProcessState(process_id=process_id)
            run.processes[process_id] = process
        process.node_id = node_id
        process.pid = event.pid
        process.argv = tuple(str(value) for value in event.argv)
        process.cwd = str(event.cwd or "")
        provenance = (event.metadata or {}).get("provenance") if isinstance(event.metadata, dict) else {}
        if isinstance(provenance, dict):
            process.provenance = deepcopy(provenance)
        process.status = "running"
        process.started_at = process.started_at or event.timestamp
        if node_id:
            node = run.nodes[node_id]
            if process_id not in node.process_ids:
                node.process_ids.append(process_id)

    def _process_output(self, run: ExecutionRunState, event: "ProcessOutputReceived") -> None:
        process_id = str(event.process_id or "").strip()
        if not process_id:
            return
        process = run.processes.get(process_id)
        if process is None:
            process = ExecutionProcessState(process_id=process_id, node_id=str(event.node_id or ""))
            run.processes[process_id] = process
        stream = str(event.stream or "stdout").strip().lower()
        if stream == "stderr":
            process.stderr_events += 1
        else:
            process.stdout_events += 1
        process.last_output = str(event.text or "")[-MAX_PROCESS_LAST_OUTPUT_CHARS:]

    def _process_exited(self, run: ExecutionRunState, event: "ProcessExited") -> None:
        process_id = str(event.process_id or "").strip()
        if not process_id:
            return
        process = run.processes.get(process_id)
        if process is None:
            process = ExecutionProcessState(process_id=process_id, node_id=str(event.node_id or ""))
            run.processes[process_id] = process
        process.exit_code = int(event.exit_code)
        process.duration_seconds = float(event.duration_seconds or 0.0)
        process.failure_kind = str(event.failure_kind or process.failure_kind or "")
        process.log_path = bounded_log_ref(event.log_path or process.log_path or "")
        process.evidence = merge_bounded_evidence(process.evidence, event.evidence)
        process.finished_at = event.timestamp
        process.status = "succeeded" if process.exit_code == 0 else ("cancelled" if process.exit_code == 130 else "failed")
        if process.node_id and process.node_id in run.nodes and process.exit_code != 0:
            node = run.nodes[process.node_id]
            node.failure_process_id = process_id
            if process.failure_kind:
                node.failure_kind = process.failure_kind
            node.evidence_refs[:] = merge_bounded_evidence(
                node.evidence_refs,
                (*process.evidence, process.log_path),
            )

    def _diagnostic(self, run: ExecutionRunState, event: "DiagnosticEmitted") -> None:
        node_id = self._resolve_existing_node_id(run, event, allow_active_match=True)
        diagnostic_id = str(event.diagnostic_id or "").strip() or f"{event.event_id}:diagnostic"
        diagnostic = ExecutionDiagnosticState(
            diagnostic_id=diagnostic_id,
            node_id=node_id,
            severity=str(event.severity or "warning"),
            code=str(event.code or ""),
            message=str(event.message or ""),
            file=str(event.file or ""),
            line=event.line,
            column=event.column,
            process_id=str(event.process_id or ""),
            failure_kind=str(event.failure_kind or ""),
            log_path=bounded_log_ref(event.log_path or ""),
            evidence=merge_bounded_evidence((), event.evidence),
            timestamp=event.timestamp,
            source=str(event.source or ""),
        )
        run.diagnostics[diagnostic_id] = diagnostic
        if node_id:
            node = run.nodes[node_id]
            if diagnostic_id not in node.diagnostic_ids:
                node.diagnostic_ids.append(diagnostic_id)
            if diagnostic.process_id and diagnostic.process_id not in node.process_ids:
                # The process may be reduced slightly later through a legacy
                # bridge; retaining the reference here keeps failure ownership
                # explicit without fabricating a process record.
                pass
            if diagnostic.failure_kind and not node.failure_kind:
                node.failure_kind = diagnostic.failure_kind
            node.evidence_refs[:] = merge_bounded_evidence(
                node.evidence_refs,
                (*diagnostic.evidence, diagnostic.log_path),
            )
            if diagnostic.severity in {"error", "fatal"} and not node.primary_failure_diagnostic_id:
                node.primary_failure_diagnostic_id = diagnostic_id
            if diagnostic.severity in {"error", "fatal"} and diagnostic.process_id:
                node.failure_process_id = diagnostic.process_id

    def _artifact(self, run: ExecutionRunState, event: "ArtifactProduced") -> None:
        producer_node_id = str(event.producer_node_id or event.node_id or "")
        if producer_node_id and producer_node_id in run.nodes:
            node_id = producer_node_id
        else:
            node_id = self._resolve_existing_node_id(
                run,
                event,
                label=str(event.producer_task or ""),
                allow_active_match=True,
            )
        requested_id = str(event.artifact_id or "").strip()
        # A live Gradle artifact event may be followed by the persistent artifact
        # registry event for the same producer/path. Merge those observations
        # instead of showing the UI two artifacts for one file.
        artifact = None
        if event.path:
            for candidate in run.artifacts.values():
                if candidate.path == str(event.path) and candidate.producer_node_id == str(node_id or producer_node_id):
                    artifact = candidate
                    break
        if artifact is None:
            artifact_id = requested_id or f"{event.event_id}:artifact"
            artifact = ExecutionArtifactState(artifact_id=artifact_id)
            run.artifacts[artifact_id] = artifact
        else:
            artifact_id = artifact.artifact_id
            if requested_id and requested_id != artifact_id:
                artifact.registry_artifact_id = requested_id
        artifact.node_id = str(node_id or "")
        artifact.producer_node_id = str(producer_node_id or node_id or "")
        artifact.path = str(event.path or artifact.path or "")
        artifact.sha256 = str(event.sha256 or artifact.sha256 or "")
        artifact.size = event.size if event.size is not None else artifact.size
        artifact.artifact_type = str(event.artifact_type or artifact.artifact_type or "")
        artifact.producer_task = str(event.producer_task or artifact.producer_task or "")
        artifact.process_id = str(event.process_id or artifact.process_id or "")
        artifact.verification_status = str(event.verification_status or artifact.verification_status or "")
        artifact.timestamp = event.timestamp or artifact.timestamp
        artifact.source = str(event.source or artifact.source or "")
        if node_id:
            node = run.nodes[node_id]
            if artifact_id not in node.artifact_ids:
                node.artifact_ids.append(artifact_id)

    def _metric(self, run: ExecutionRunState, event: "MetricChanged") -> None:
        node_id = self._resolve_existing_node_id(run, event, allow_active_match=True)
        if node_id:
            run.nodes[node_id].metrics[str(event.name)] = event.value
        else:
            run.metrics[str(event.name)] = event.value

    def _event_label(self, event: "DevtoolEvent") -> str:
        for attr in ("label", "task", "producer_task"):
            value = str(getattr(event, attr, "") or "").strip()
            if value:
                return value
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        for key in ("task", "name", "phase", "command"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value
        return ""

    def _ensure_event_node(
        self,
        run: ExecutionRunState,
        event: "DevtoolEvent",
        *,
        label: str = "",
        kind: str = "operation",
        allow_active_match: bool = False,
    ) -> ExecutionNodeState:
        node_id = self._resolve_existing_node_id(
            run,
            event,
            label=label,
            allow_active_match=allow_active_match,
        )
        if not node_id:
            parent = str(event.parent_id or run.run_id)
            local_label = str(label or self._event_label(event) or event.protocol_type)
            node_id = str(
                NodeId.dynamic(
                    parent_id=parent,
                    kind=kind or "operation",
                    key=local_label,
                    instance=str(event.sequence),
                )
            )
            event.node_id = node_id
        node = run.nodes.get(node_id)
        if node is None:
            if run.graph_sealed:
                raise ExecutionTransitionError(
                    f"execution graph {run.run_id!r} is sealed; cannot discover node {node_id!r}"
                )
            node = ExecutionNodeState(
                node_id=node_id,
                parent_id=str(event.parent_id or ""),
                label=str(label or self._event_label(event) or ""),
                kind=str(kind or "operation"),
                discovered_at=event.timestamp,
            )
            run.nodes[node_id] = node
        return node

    def _resolve_existing_node_id(
        self,
        run: ExecutionRunState,
        event: "DevtoolEvent",
        *,
        label: str = "",
        allow_active_match: bool = False,
    ) -> str:
        explicit = str(event.node_id or "").strip()
        if explicit:
            event.node_id = str(NodeId(explicit))
            return event.node_id
        if not allow_active_match:
            return ""
        desired_label = str(label or self._event_label(event) or "").strip()
        candidates = [node for node in run.nodes.values() if node.status is ExecutionStatus.RUNNING]
        if desired_label:
            labelled = [node for node in candidates if node.label == desired_label]
            if len(labelled) == 1:
                event.node_id = labelled[0].node_id
                return event.node_id
        if len(candidates) == 1:
            event.node_id = candidates[0].node_id
            return event.node_id
        return ""

    @staticmethod
    def _require_nonterminal(node: ExecutionNodeState, action: str) -> None:
        if node.status.terminal:
            raise ExecutionTransitionError(
                f"cannot {action} terminal node {node.node_id!r} with status {node.status.value!r}"
            )

    @staticmethod
    def _set_terminal_node_status(node: ExecutionNodeState, status: ExecutionStatus) -> None:
        if node.status.terminal and node.status is not status:
            raise ExecutionTransitionError(
                f"node {node.node_id!r} already ended as {node.status.value!r}, cannot become {status.value!r}"
            )
        node.status = status


class ExecutionStore:
    """Thread-safe multi-run execution state store."""

    def __init__(self, *, reducer: ExecutionReducer | None = None) -> None:
        self.reducer = reducer or ExecutionReducer()
        self._runs: dict[str, ExecutionRunState] = {}
        self._last_run_id: str = ""
        self._lock = RLock()

    def apply(self, event: "DevtoolEvent") -> ExecutionSnapshot:
        run_id = str(event.run_id or "").strip()
        if not run_id:
            raise ExecutionStoreError("execution event must have run_id before store reduction")
        if int(event.sequence) < 0:
            raise ExecutionSequenceError("execution event must have sequence before store reduction")
        if not str(event.event_id or "").strip():
            raise ExecutionStoreError("execution event must have event_id before store reduction")
        with self._lock:
            current = self._runs.get(run_id)
            if current is None:
                current = ExecutionRunState(run_id=str(RunId(run_id)))
            # Reduction is transactional: an invalid transition/event cannot
            # leave partially-created nodes, diagnostics, or process state in
            # the authoritative store.
            candidate = deepcopy(current)
            self.reducer.apply(candidate, event)
            self._runs[run_id] = candidate
            self._last_run_id = run_id
            return ExecutionSnapshot.from_state(candidate)

    def accounting(
        self,
        run_id: str | None = None,
        *,
        scope: str = "recursive",
        parent_id: str = "",
    ) -> ExecutionAccountingScope:
        with self._lock:
            resolved = str(run_id or self._last_run_id or "").strip()
            if not resolved or resolved not in self._runs:
                raise KeyError(f"Unknown execution run: {resolved or '<none>'}")
            return execution_accounting_scope(self._runs[resolved], scope=scope, parent_id=parent_id)

    def apply_many(self, events: Iterable["DevtoolEvent"]) -> ExecutionSnapshot | None:
        snapshot: ExecutionSnapshot | None = None
        for event in events:
            snapshot = self.apply(event)
        return snapshot

    def plan_graph(self, graph: "ExecutionGraph", *, run_id: str | None = None) -> ExecutionSnapshot:
        resolved_run_id = str(run_id or self._last_run_id or RunId.current(create=True))
        with self._lock:
            run = self._runs.get(resolved_run_id)
            if run is None:
                run = ExecutionRunState(run_id=str(RunId(resolved_run_id)))
                self._runs[resolved_run_id] = run
            self.reducer.plan_graph(run, graph)
            self._last_run_id = resolved_run_id
            return ExecutionSnapshot.from_state(run)

    def has_event(self, run_id: str, event_id: str) -> bool:
        with self._lock:
            run = self._runs.get(str(run_id or ""))
            return bool(run is not None and str(event_id or "") in run.applied_event_ids)

    def snapshot(self, run_id: str | None = None) -> ExecutionSnapshot | None:
        with self._lock:
            resolved = str(run_id or self._last_run_id or "")
            if not resolved:
                return None
            run = self._runs.get(resolved)
            return ExecutionSnapshot.from_state(run) if run is not None else None

    def snapshots(self) -> tuple[ExecutionSnapshot, ...]:
        with self._lock:
            return tuple(ExecutionSnapshot.from_state(run) for run in self._runs.values())

    def clear(self, run_id: str | None = None) -> None:
        with self._lock:
            if run_id is None:
                self._runs.clear()
                self._last_run_id = ""
                return
            resolved = str(run_id)
            self._runs.pop(resolved, None)
            if self._last_run_id == resolved:
                self._last_run_id = next(reversed(self._runs), "") if self._runs else ""


__all__ = [
    "ExecutionStoreError",
    "ExecutionSequenceError",
    "ExecutionTransitionError",
    "ExecutionReducer",
    "ExecutionStore",
]
