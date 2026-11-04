"""DN03-R4: append a fail-closed, same-run acceptance job to an RM-09 native DAG.

The R3/RM-06 compiler remains authoritative. This tool only adds a projection
that downloads and rechecks each native job's local evidence and declared bytes.
It does not authenticate GitHub's artifact service, sign outputs, or award release
qualification. A real consumer-hosted run remains necessary for acceptance.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

from . import actions_dn03_dotnet as dn03
from . import actions_dn03_r3 as r3

CHECKOUT = "d23441a48e516b6c34aea4fa41551a30e30af803"
SETUP_PYTHON = "ece7cb06caefa5fff74198d8649806c4678c61a1"
DOWNLOAD = "9000827ccba6bdab643e8b6fd33ac0654aef8333"
UPLOAD = "cf430e030ddbb5b0abf93d22962f4752f3646cd9"
ACCEPTANCE_JOB = "dn03_r4_acceptance"
MAX_JOBS = 24


class R4Error(ValueError):
    """A reviewed source, DAG, path, or output requirement was not satisfied."""


def _job_list(graph: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = graph.get("jobs")
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= MAX_JOBS:
        raise R4Error("R4 requires 1–24 native .NET jobs")
    names = []
    for job in jobs:
        if not isinstance(job, dict) or any(not dn03.SLUG.fullmatch(str(job.get(k, ""))) for k in ("id",)):
            raise R4Error("R4 native job has an invalid identifier")
        if job.get("provider") != "dotnet" or job.get("profile_id") != "ubuntu-24.04-x64":
            raise R4Error("R4 refuses a non-Linux/non-.NET node")
        if not isinstance(job.get("outputs"), list):
            raise R4Error("R4 missing authoritative native output declarations")
        if job["id"] == ACCEPTANCE_JOB:
            raise R4Error("R4 acceptance job would collide with a native DAG job")
        names.append(job["id"])
    if len(set(names)) != len(names):
        raise R4Error("R4 duplicate native job IDs")
    return jobs


def _append_acceptance(original: str, graph: dict[str, Any], *,
                       target: str, workflow: str, devtool_sha: str) -> str:
    """Append only one observer job; never change native dependencies or commands."""
    jobs = _job_list(graph)
    for ident in (target, workflow):
        if not dn03.SLUG.fullmatch(ident):
            raise R4Error("R4 target or workflow is not a bounded slug")
    if not dn03.SHA.fullmatch(devtool_sha) or graph.get("runtime_sha") != devtool_sha:
        raise R4Error("R4 Devtool commit is not the reviewed 40-character pin")
    digest = graph.get("graph_sha256", "")
    if not isinstance(digest, str) or not dn03.DIGEST.fullmatch(digest):
        raise R4Error("R4 graph digest is malformed")
    if original.count("\njobs:\n") != 1 or original.count(f"  {ACCEPTANCE_JOB}:\n"):
        raise R4Error("R4 cannot locate an unmodified native job boundary")
    for job in jobs:
        if original.count(f"  {job['id']}:\n") != 1:
            raise R4Error("R4 native node is absent or duplicated in R3 workflow")
    if original.count("      - name: DN03-R3 reverify native .NET node, host and output bytes\n") != len(jobs):
        raise R4Error("R4 requires post-execution R3 verification on every native node")
    if not original.endswith("\n"):
        raise R4Error("R4 native workflow must be newline-terminated")

    namespace = f"devtool-{target}-{workflow}-multi"
    needs = ", ".join(job["id"] for job in jobs)
    lines = [
        f"  {ACCEPTANCE_JOB}:",
        f"    name: DN03-R4 verify {len(jobs)} hosted native .NET nodes (non-release)",
        f"    needs: [{needs}]",
        "    if: ${{ always() }}",
        "    runs-on: ubuntu-24.04",
        "    timeout-minutes: 35",
        "    permissions:",
        "      contents: read",
        "      actions: read",
        "    env:",
        f"      DEVTOOL_SOURCE_SHA: '{devtool_sha}'",
        f"      DN03_R4_GRAPH_SHA: '{digest}'",
        "      DN03_R4_NEEDS_JSON: ${{ toJson(needs) }}",
        "      PYTHONDONTWRITEBYTECODE: '1'",
        "    steps:",
        "      - name: Checkout exact consumer source",
        f"        uses: actions/checkout@{CHECKOUT}",
        "        with:",
        "          persist-credentials: false",
        "      - name: Pinned Python for immutable Devtool verifier",
        f"        uses: actions/setup-python@{SETUP_PYTHON}",
        "        with:",
        "          python-version: '3.13'",
        "          check-latest: false",
        "      - name: Bind source and independent runner-local receipt root",
        "        shell: bash",
        "        run: |",
        "          set -euo pipefail",
        '          test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
        '          test "${GITHUB_ACTIONS:-}" = true',
        '          test "${RUNNER_OS:-}" = Linux',
        '          test "${RUNNER_ARCH:-}" = X64',
        '          test -n "${DN03_R4_NEEDS_JSON:-}"',
        '          mkdir -p "$RUNNER_TEMP/dn03-r4/evidence" "$RUNNER_TEMP/dn03-r4/host" "$RUNNER_TEMP/dn03-r4/bytes"',
        '          printf "%s/%s/%s\\n" "$GITHUB_SHA" "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT" > "$RUNNER_TEMP/dn03-r4/source-run.txt"',
        "      - name: Acquire immutable Devtool verifier source",
        "        shell: bash",
        "        run: |",
        "          set -euo pipefail",
        '          test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
        '          git clone --no-checkout https://github.com/Mikeyphw/devtool.git "$RUNNER_TEMP/dn03-r4-devtool-src"',
        '          git -C "$RUNNER_TEMP/dn03-r4-devtool-src" checkout --detach "$DEVTOOL_SOURCE_SHA"',
        '          test "$(git -C "$RUNNER_TEMP/dn03-r4-devtool-src" rev-parse HEAD)" = "$DEVTOOL_SOURCE_SHA"',
        '          python3 -m venv "$RUNNER_TEMP/dn03-r4-venv"',
        '          "$RUNNER_TEMP/dn03-r4-venv/bin/python" -m pip install --disable-pip-version-check "$RUNNER_TEMP/dn03-r4-devtool-src"',
        '          "$RUNNER_TEMP/dn03-r4-venv/bin/python" -c "from devtool.actions_dn03_r4_verify import verify_workflow"',
    ]
    for job in jobs:
        jid = job["id"]
        lines += [
            f"      - name: Fetch {jid} typed native evidence",
            f"        uses: actions/download-artifact@{DOWNLOAD}",
            "        with:",
            f"          name: {namespace}-{jid}-evidence",
            f"          path: ${{{{ runner.temp }}}}/dn03-r4/evidence/{jid}",
            f"      - name: Fetch {jid} DN03 host evidence",
            f"        uses: actions/download-artifact@{DOWNLOAD}",
            "        with:",
            f"          name: dn03-host-{jid}-${{{{ github.run_id }}}}-${{{{ github.run_attempt }}}}",
            f"          path: ${{{{ runner.temp }}}}/dn03-r4/host/{jid}",
        ]
        if job["outputs"]:
            lines += [
                f"      - name: Fetch {jid} native output bytes",
                f"        uses: actions/download-artifact@{DOWNLOAD}",
                "        with:",
                f"          name: {namespace}-{jid}-bytes",
                f"          path: ${{{{ runner.temp }}}}/dn03-r4/bytes/{jid}",
            ]
    lines += [
        "      - name: DN03-R4 cross-check all same-run native node receipts",
        "        shell: bash",
        "        run: |",
        "          set -euo pipefail",
        '          test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
        '          "$RUNNER_TEMP/dn03-r4-venv/bin/python" -m devtool.actions_dn03_r4_verify verify-workflow \\',
        '            --repo "$GITHUB_WORKSPACE" \\',
        f'            --target {target} --workflow {workflow} \\',
        f'            --devtool-sha {devtool_sha} --graph-sha {digest} \\',
        '            --evidence "$RUNNER_TEMP/dn03-r4"',
        "      - name: Seal available evidence even on failure",
        "        if: always()",
        "        shell: bash",
        "        run: |",
        "          set -euo pipefail",
        '          mkdir -p "$RUNNER_TEMP/dn03-r4"',
        '          (cd "$RUNNER_TEMP/dn03-r4" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 -r sha256sum > SHA256SUMS)',
        "      - name: Upload non-release R4 cross-node review receipt",
        "        if: always()",
        f"        uses: actions/upload-artifact@{UPLOAD}",
        "        with:",
        "          name: dn03-r4-acceptance-${{ github.run_id }}-${{ github.run_attempt }}",
        "          path: |",
        "            ${{ runner.temp }}/dn03-r4/acceptance.json",
        "            ${{ runner.temp }}/dn03-r4/source-run.txt",
        "            ${{ runner.temp }}/dn03-r4/SHA256SUMS",
        "          if-no-files-found: warn",
        "          retention-days: 14",
    ]
    return original + "\n".join(lines) + "\n"


def compile_r4(root: Path, *, target: str, workflow: str,
               devtool_sha: str) -> tuple[str, dict[str, Any]]:
    original, prior = r3.compile_r3(root, target=target,
                                    workflow=workflow, devtool_sha=devtool_sha)
    graph = dn03.compile_graph(root.resolve(), target=target, workflow=workflow,
                               devtool_sha=devtool_sha,
                               host_profiles="ubuntu-24.04-x64", cache_locks=False)
    if (graph.get("graph_sha256") != prior.get("graph_sha256") or
            graph.get("provider_plan_sha256") != prior.get("provider_plan_sha256") or
            graph.get("runtime_sha") != devtool_sha or
            [j.get("node") for j in graph.get("jobs", [])] != prior.get("native_nodes")):
        raise R4Error("R4 native graph drifted during source-bound export")
    enhanced = _append_acceptance(original, graph, target=target,
                                  workflow=workflow, devtool_sha=devtool_sha)
    receipt = {**prior,
               "schema_version": 1, "kind": "devtool.dn03-r4-cross-node-export",
               "workflow_sha256": sha256(enhanced.encode("utf-8")).hexdigest(),
               "expected_node_count": len(graph["jobs"]),
               "same_run_cross_node_verification_required": True,
               "real_generated_workflow_executed": False,
               "remote_authenticated": False,
               "release_qualified": False,
               "generator": "DN03-R4 adds a verifier job to DN03-R3/RM-06 native projection"}
    return enhanced, receipt


def _paths(root: Path, target: str, workflow: str) -> tuple[Path, Path]:
    stem = f"devtool-dn03r4-{target}-{workflow}"
    return (dn03._path(root, ".github/workflows/" + stem + ".yml"),
            dn03._path(root, "docs/ci/" + stem + ".json"))


def export(root: Path, *, target: str, workflow: str, devtool_sha: str,
           mode: str, confirmed: bool = False) -> dict[str, Any]:
    if mode not in ("plan", "write", "verify"):
        raise R4Error("R4 requires plan/write/verify")
    root = root.resolve()
    result, record = compile_r4(root, target=target,
                                workflow=workflow, devtool_sha=devtool_sha)
    paths = _paths(root, target, workflow)
    expected = ((paths[0], result.encode("utf-8")),
                (paths[1], (json.dumps(record, sort_keys=True, indent=2) + "\n").encode("utf-8")))
    if mode == "write":
        if not confirmed:
            raise R4Error("R4 requires explicit --yes for write")
        if dn03._git(root, "status", "--porcelain", "--untracked-files=all"):
            raise R4Error("R4 refuses dirty consumer worktree")
        if any(p.exists() or p.is_symlink() for p, _ in expected):
            raise R4Error("R4 refuses existing workflow/receipt destinations")
        before = dn03._git(root, "rev-parse", "HEAD")
        created: list[Path] = []
        try:
            for path, payload in expected:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as file:
                    file.write(payload)
                created.append(path)
            if dn03._git(root, "rev-parse", "HEAD") != before:
                raise R4Error("R4 Git source changed during export")
        except BaseException:
            for path in reversed(created):
                path.unlink(missing_ok=True)
            raise
    if mode == "verify":
        if any(not p.is_file() or p.is_symlink() or p.read_bytes() != data for p, data in expected):
            raise R4Error("R4 workflow/receipt differs from reviewed graph")
    return {**record, "mode": mode, "wrote_files": mode == "write",
            "verified_files": mode == "verify",
            "workflow_path": str(paths[0].relative_to(root)),
            "receipt_path": str(paths[1].relative_to(root))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "write", "verify"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--devtool-sha", required=True)
    parser.add_argument("--yes", action="store_true")
    ns = parser.parse_args(argv)
    try:
        record = export(ns.repo, target=ns.target, workflow=ns.workflow,
                        devtool_sha=ns.devtool_sha, mode=ns.mode,
                        confirmed=ns.yes)
    except (R4Error, dn03.DN03Error, r3.R3ExportError, ValueError,
            OSError, TypeError, KeyError) as exc:
        print("DN03_R4_REFUSED: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(record, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
