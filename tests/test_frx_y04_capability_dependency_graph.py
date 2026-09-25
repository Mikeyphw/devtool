from __future__ import annotations

from pathlib import Path
import subprocess
import io
import json
from contextlib import redirect_stdout

import pytest

from devtool.config import load_project_config
from devtool.cli import _federation_command, build_parser
from devtool.federation_capabilities import (
    FederationCapabilityError,
    build_capability_graph,
    project_capability_dependencies,
)
from devtool.federation_gate import build_federation_gate1_report
from devtool.federation_plan import build_federated_plan
from devtool.federation_scheduler import build_federated_schedule


def _child(root: Path, workflow: str = "validate") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".devtool.toml").write_text(
        f'''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\ncommand=["echo","ok"]\ninputs=["src/**"]\noutputs=["out/**"]\n[targets.app.workflows]\n{workflow}=["job:check"]\n''',
        encoding="utf-8",
    )


def _parent(tmp_path: Path, federation: str):
    parent = tmp_path / "parent"
    provider = tmp_path / "provider"
    consumer = tmp_path / "consumer"
    transitive = tmp_path / "transitive"
    parent.mkdir()
    _child(provider)
    _child(consumer)
    _child(transitive)
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n'''
        + federation,
        encoding="utf-8",
    )
    return load_project_config(
        parent,
        parent / ".devtool.toml",
        target_name="",
        allow_ambiguous_target=True,
        allow_ambiguous_project_kind=True,
    )


def test_y04_parses_versioned_exports_and_explicit_compatibility(tmp_path: Path) -> None:
    config = _parent(tmp_path, '''\n[federation]\nschema=1\n[federation.repositories.provider]\npath="../provider"\n[federation.repositories.provider.exports]\ncapabilities=["securefs/v1", "securefs/v2"]\n[federation.repositories.consumer]\npath="../consumer"\n[federation.repositories.consumer.requires]\ncapabilities=[{name="securefs/v1", compatible=["securefs/v2"], policy="explicit-list"}]\n''')
    req = config.federation.repositories["consumer"].requires[0]
    assert req.accepted_capabilities == ("securefs/v1", "securefs/v2")
    graph = build_capability_graph(config)
    assert graph.edges[0].provider == "provider"
    assert graph.edges[0].consumer == "consumer"
    assert graph.edges[0].matched_capability in {"securefs/v1", "securefs/v2"}


def test_y04_rejects_unversioned_capability_identifier(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="versioned capability identifier"):
        _parent(tmp_path, '''\n[federation]\nschema=1\n[federation.repositories.provider]\npath="../provider"\n[federation.repositories.provider.exports]\ncapabilities=["securefs"]\n''')


def test_y04_missing_and_ambiguous_mandatory_providers_fail_closed(tmp_path: Path) -> None:
    missing = _parent(tmp_path, '''\n[federation]\nschema=1\n[federation.repositories.consumer]\npath="../consumer"\n[federation.repositories.consumer.requires]\ncapabilities=["securefs/v1"]\n''')
    with pytest.raises(FederationCapabilityError, match="no enrolled repository exports"):
        build_capability_graph(missing)

    tmp2 = tmp_path / "ambiguous"
    tmp2.mkdir()
    config = _parent(tmp2, '''\n[federation]\nschema=1\n[federation.repositories.provider]\npath="../provider"\n[federation.repositories.provider.exports]\ncapabilities=["securefs/v1"]\n[federation.repositories.transitive]\npath="../transitive"\n[federation.repositories.transitive.exports]\ncapabilities=["securefs/v1"]\n[federation.repositories.consumer]\npath="../consumer"\n[federation.repositories.consumer.requires]\ncapabilities=["securefs/v1"]\n''')
    with pytest.raises(FederationCapabilityError, match="multiple enrolled providers"):
        build_capability_graph(config)


def test_y04_optional_unresolved_requirement_is_visible_not_fatal(tmp_path: Path) -> None:
    config = _parent(tmp_path, '''\n[federation]\nschema=1\n[federation.repositories.consumer]\npath="../consumer"\n[federation.repositories.consumer.requires]\ncapabilities=[{name="optional-api/v1", optional=true}]\n''')
    graph = build_capability_graph(config)
    assert graph.healthy
    assert graph.resolutions[0].status == "optional-missing"
    assert graph.edges == ()


def test_y04_plan_and_scheduler_enforce_capability_provider_before_consumer(tmp_path: Path) -> None:
    config = _parent(tmp_path, '''\n[federation]\nschema=1\n[federation.repositories.provider]\npath="../provider"\n[federation.repositories.provider.exports]\ncapabilities=["securefs/v1"]\n[federation.repositories.consumer]\npath="../consumer"\n[federation.repositories.consumer.requires]\ncapabilities=["securefs/v1"]\n[federation.workflows.release]\nsteps=[\n {id="consumer",ref="consumer::app::validate"},\n {id="provider",ref="provider::app::validate"},\n]\n''')
    graph = build_federated_plan(config, "release")
    provider_nodes = [n for n in graph.nodes if n.repository == "provider"]
    consumer_roots = [n for n in graph.nodes if n.repository == "consumer" and n.metadata.get("capability_dependencies")]
    assert provider_nodes and consumer_roots
    assert set(consumer_roots[0].depends_on).intersection({n.key for n in provider_nodes})
    assert graph.as_dict()["capability_graph"]["edges"][0]["reason"].startswith("consumer requires securefs/v1")

    workflow = project_capability_dependencies(config, config.federation.workflows["release"])
    consumer_step = next(step for step in workflow.steps if step.step_id == "consumer")
    assert "provider" in consumer_step.depends_on
    schedule = build_federated_schedule(workflow, graph, parallel_requested=True, max_parallel=2)
    waves = [set(w.nodes) for w in schedule.waves]
    assert any("provider" in wave for wave in waves)
    provider_wave = next(i for i, wave in enumerate(waves) if "provider" in wave)
    consumer_wave = next(i for i, wave in enumerate(waves) if "consumer" in wave)
    assert provider_wave < consumer_wave


def test_y04_exact_requirement_does_not_silently_select_other_capability_version(tmp_path: Path) -> None:
    config = _parent(tmp_path, '''\n[federation]\nschema=1\n[federation.repositories.provider]\npath="../provider"\n[federation.repositories.provider.exports]\ncapabilities=["securefs/v2"]\n[federation.repositories.consumer]\npath="../consumer"\n[federation.repositories.consumer.requires]\ncapabilities=["securefs/v1"]\n''')
    with pytest.raises(FederationCapabilityError):
        build_capability_graph(config)


def test_y04_federation_preflight_surfaces_capability_resolution_failure(tmp_path: Path) -> None:
    config = _parent(tmp_path, '''\n[federation]\nschema=1\n[federation.repositories.consumer]\npath="../consumer"\n[federation.repositories.consumer.requires]\ncapabilities=["securefs/v1"]\n''')
    report = build_federation_gate1_report(config)
    check = next(item for item in report.checks if item.section == "capabilities")
    assert check.status == "error"
    assert "no enrolled repository exports" in check.detail


def test_y04_cli_capability_graph_surface_is_machine_readable(tmp_path: Path) -> None:
    config = _parent(tmp_path, '''
[federation]
schema=1
[federation.repositories.provider]
path="../provider"
[federation.repositories.provider.exports]
capabilities=["securefs/v1"]
[federation.repositories.consumer]
path="../consumer"
[federation.repositories.consumer.requires]
capabilities=["securefs/v1"]
''')
    args = build_parser().parse_args(["-r", str(config.repo_root), "federation", "capabilities", "--json"])
    out = io.StringIO()
    with redirect_stdout(out):
        assert _federation_command(args) == 0
    payload = json.loads(out.getvalue())
    assert payload["healthy"] is True
    assert payload["edges"][0]["provider"] == "provider"
    assert payload["edges"][0]["consumer"] == "consumer"


def test_y04_exact_identifier_is_preferred_over_compatible_alias_from_same_provider(tmp_path: Path) -> None:
    config = _parent(tmp_path, '''\n[federation]\nschema=1\n[federation.repositories.provider]\npath="../provider"\n[federation.repositories.provider.exports]\ncapabilities=["securefs/v2", "securefs/v1"]\n[federation.repositories.consumer]\npath="../consumer"\n[federation.repositories.consumer.requires]\ncapabilities=[{name="securefs/v1", compatible=["securefs/v2"], policy="explicit-list"}]\n''')
    graph = build_capability_graph(config)
    assert graph.edges[0].matched_capability == "securefs/v1"
