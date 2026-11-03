"""DN03: deterministic, fail-closed, source-bound .NET native DAG workflow export.

Uses RM-06's graph and runner, never invents a second task DAG. A generated
workflow and local sidecar are review artifacts, not authentication or release
qualification. No GitHub mutation or external download occurs at generation.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from .actions_graph_export import compile_graph, workflow_yaml
from .dn03_runtime_only import SHA512 as RUNTIME_ARCHIVE_SHA512
from .actions_language_providers import ProviderError

SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}\Z")
SDK = re.compile(r"(?:8|9|10)\.0\.[0-9]{1,4}\Z")
UPLOADER = "cf430e030ddbb5b0abf93d22962f4752f3646cd9"  # actions/upload-artifact v7


class DN03Error(ValueError):
    pass


def _git(root: Path, *args: str) -> str:
    try:
        run = subprocess.run(["git", "-C", str(root), *args], text=True,
                             capture_output=True, check=False, timeout=12)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DN03Error("DN03 cannot inspect consumer Git source") from exc
    if run.returncode:
        raise DN03Error("DN03 consumer Git operation failed: " + " ".join(args[:2]))
    return run.stdout.strip()


def _path(root: Path, value: str) -> Path:
    if (not isinstance(value, str) or not value or len(value) > 220 or
            "\\" in value or value.startswith("/") or
            any(piece in ("", ".", "..") or
                (piece.startswith(".") and not (i == 0 and piece == ".github"))
                for i, piece in enumerate(value.split("/"))) or
            not re.fullmatch(r"[A-Za-z0-9_./-]+", value)):
        raise DN03Error("DN03 output path must be a bounded relative file")
    here = root
    for piece in value.split("/"):
        here = here / piece
        if here.is_symlink():
            raise DN03Error("DN03 refuses symlinked output path")
    if here.exists() and not here.is_file():
        raise DN03Error("DN03 output is not a regular file")
    return here


def _isolated_sdk_step() -> str:
    """Keep only the reviewed SDK in a dedicated install root."""
    return (
        "      - name: DN03 bind isolated exact .NET SDK installation\n"
        "        shell: bash\n"
        "        run: |\n"
        "          set -euo pipefail\n"
        "          mkdir -p \"$RUNNER_TEMP/dn03-dotnet\"\n"
        "          printf 'DOTNET_INSTALL_DIR=%s\\n' \"$RUNNER_TEMP/dn03-dotnet\" >> \"$GITHUB_ENV\"\n"
    )


def _runtime_only_step(jid: str) -> str:
    if not SLUG.fullmatch(jid):
        raise DN03Error('DN03 unreviewed legacy runtime node')
    return (
        "      - name: DN03 install SHA-512-pinned .NET 6 runtime only\n"
        "        shell: bash\n"
        "        run: |\n"
        "          set -euo pipefail\n"
        f"          mkdir -p \"$RUNNER_TEMP/dn03-host/{jid}\"\n"
        "          \"$RUNNER_TEMP/devtool-venv/bin/python\" -m devtool.dn03_runtime_only install "
        "--download "
        "--archive \"$RUNNER_TEMP/dn03-runtime-6.0.36-linux-x64.tar.gz\" "
        "--dotnet-root \"$DOTNET_INSTALL_DIR\" "
        f"--receipt \"$RUNNER_TEMP/dn03-host/{jid}/runtime.json\"\n"
        "          dotnet --list-runtimes | grep -F 'Microsoft.NETCore.App 6.0.36 ['\n"
    )


def _host_step(job: dict[str, Any], devtool_sha: str, graph_sha: str) -> str:
    # No bash interpolation in these values: they are fully reviewed literals.
    jid = job["id"]
    sdk = job["toolchain_version"]
    runtime = job.get("dotnet_legacy_runtime", "")
    if not (SLUG.fullmatch(jid) and SDK.fullmatch(sdk) and
            runtime in ("", "6.0.36") and SHA.fullmatch(devtool_sha) and
            DIGEST.fullmatch(graph_sha)):
        raise DN03Error("DN03 unreviewed .NET node, SDK or graph identity")
    return (
        "      - name: DN03 verify .NET host and record local source-bound evidence\n"
        "        shell: bash\n"
        "        env:\n"
        f"          DN03_NODE: '{jid}'\n"
        f"          DN03_EXPECTED_SDK: '{sdk}'\n"
        f"          DN03_EXPECTED_RUNTIME: '{runtime}'\n"
        f"          DN03_GRAPH_SHA: '{graph_sha}'\n"
        "        run: |\n"
        "          set -euo pipefail\n"
        "          test \"${GITHUB_ACTIONS:-}\" = true\n"
        "          test \"${RUNNER_OS:-}\" = Linux\n"
        "          test \"${RUNNER_ARCH:-}\" = X64\n"
        f"          test \"$DEVTOOL_SOURCE_SHA\" = '{devtool_sha}'\n"
        "          test \"$(dotnet --version)\" = \"$DN03_EXPECTED_SDK\"\n"
        "          if test -n \"$DN03_EXPECTED_RUNTIME\"; then\n"
        "            dotnet --list-runtimes | awk -v v=\"$DN03_EXPECTED_RUNTIME\" '\n"
        "              $1 == \"Microsoft.NETCore.App\" && $2 == v { seen++ }\n"
        "              END { exit(seen == 1 ? 0 : 1) }'\n"
        "          fi\n"
        "          mkdir -p \"$RUNNER_TEMP/dn03-host/$DN03_NODE\"\n"
        "          python3 - <<'PY'\n"
        "          import json, os, pathlib, re\n"
        "          e = os.environ\n"
        "          sha = e.get('GITHUB_SHA', '')\n"
        "          assert re.fullmatch(r'[0-9a-f]{40}', sha)\n"
        "          run = e.get('GITHUB_RUN_ID', '')\n"
        "          attempt = e.get('GITHUB_RUN_ATTEMPT', '')\n"
        "          assert run.isdecimal() and attempt.isdecimal()\n"
        "          assert int(run) > 0 and int(attempt) > 0\n"
        "          value = {'kind':'devtool.dn03-host-review',\n"
        "                   'source_sha':sha,\n"
        "                   'devtool_sha':e['DEVTOOL_SOURCE_SHA'],\n"
        "                   'graph_sha256':e['DN03_GRAPH_SHA'],\n"
        "                   'node':e['DN03_NODE'],\n"
        "                   'sdk':e['DN03_EXPECTED_SDK'],\n"
        "                   'legacy_runtime':e['DN03_EXPECTED_RUNTIME'],\n"
        "                   'run_id':int(run),'run_attempt':int(attempt),\n"
        "                   'runtime_inventory_checked':True,\n"
        "                   'hosted_authenticated':False,\n"
        "                   'release_qualified':False}\n"
        "          if e['DN03_EXPECTED_RUNTIME']:\n"
        "              from hashlib import sha256\n"
        "              runtime = pathlib.Path(e['RUNNER_TEMP'])/'dn03-host'/e['DN03_NODE']/'runtime.json'\n"
        "              raw = runtime.read_bytes()\n"
        "              installed = json.loads(raw)\n"
        f"              assert installed.get('archive_sha512') == '{RUNTIME_ARCHIVE_SHA512}'\n"
        "              assert installed.get('runtime') == e['DN03_EXPECTED_RUNTIME']\n"
        "              assert installed.get('sdk_installed_by_this_provisioner') is False\n"
        "              value['runtime_receipt_sha256'] = sha256(raw).hexdigest()\n"
        "          out = pathlib.Path(e['RUNNER_TEMP'])/'dn03-host'/e['DN03_NODE']/'host.json'\n"
        "          with out.open('x', encoding='utf-8') as f:\n"
        "              json.dump(value, f, sort_keys=True, indent=2)\n"
        "              f.write('\\n')\n"
        "          PY\n"
    )


def _upload_step(jid: str) -> str:
    if not SLUG.fullmatch(jid):
        raise DN03Error("DN03 unreviewed upload node")
    return (
        "      - name: DN03 preserve local host review (non-release)\n"
        "        if: always()\n"
        f"        uses: actions/upload-artifact@{UPLOADER}\n"
        "        with:\n"
        f"          name: dn03-host-{jid}-${{{{ github.run_id }}}}-${{{{ github.run_attempt }}}}\n"
        f"          path: ${{{{ runner.temp }}}}/dn03-host/{jid}/\n"
        "          if-no-files-found: error\n"
        "          retention-days: 14\n"
    )


def _harden(yaml: str, jobs: list[dict[str, Any]], pin: str, graph_sha: str) -> str:
    """Insert DN03 host proof within each exact RM-06 job, no YAML reformatting."""
    positions = []
    for job in jobs:
        marker = f"  {job['id']}:\n"
        if yaml.count(marker) != 1:
            raise DN03Error("DN03 graph node does not have one workflow job")
        positions.append((yaml.index(marker), job))
    if positions != sorted(positions, key=lambda x: x[0]):
        raise DN03Error("DN03 workflow order diverged from native DAG")
    start = positions[0][0]
    out = [yaml[:start]]
    for i, (pos, job) in enumerate(positions):
        limit = positions[i + 1][0] if i + 1 < len(positions) else len(yaml)
        original = yaml[pos:limit]
        if original.count("      - name: Acquire immutable Devtool runtime\n") != 1:
            raise DN03Error("DN03 frozen workflow missing runtime source acquisition")
        if original.count("      - name: Pinned .NET SDK\n") != 1:
            raise DN03Error("DN03 frozen workflow missing reviewed .NET SDK setup")
        if job.get("dotnet_legacy_runtime") == "6.0.36":
            if original.count("      - name: Legacy .NET 6 runtime compatibility (EOL)\n") != 1:
                raise DN03Error("DN03 legacy runtime setup is absent")
        else:
            if "      - name: Legacy .NET 6 runtime compatibility (EOL)\n" in original:
                raise DN03Error("DN03 unexpected EOL SDK provisioner")
        # For EOL jobs replace the inherited 6.0.428 *SDK* setup with an
        # independently Microsoft-SHA512-pinned runtime-only install. The
        # module is sourced from immutable Devtool after source acquisition.
        if job.get('dotnet_legacy_runtime') == '6.0.36':
            original, count = re.subn(
                r"      - name: Legacy \.NET 6 runtime compatibility \(EOL\)\n"
                r"        uses: actions/setup-dotnet@[0-9a-f]{40}\n"
                r"        with:\n          dotnet-version: '6\.0\.428'\n"
                r"          cache: false\n",
                '', original, count=1)
            if count != 1:
                raise DN03Error('DN03 legacy SDK setup differs from reviewed source')
        original = original.replace(
            "      - name: Pinned .NET SDK\n",
            _isolated_sdk_step() + "      - name: Pinned .NET SDK\n", 1)
        original = original.replace(
            "      - name: Verify dependencies and run native target-local node\n",
            (_runtime_only_step(job['id']) if job.get('dotnet_legacy_runtime') else '') +
            _host_step(job, pin, graph_sha) +
            "      - name: Verify dependencies and run native target-local node\n", 1)
        if original.count('DN03 bind isolated exact .NET SDK installation') != 1 or original.count('DN03 verify .NET host') != 1:
            raise DN03Error('DN03 failed to inject isolated host verification')
        original = original.rstrip("\n") + "\n" + _upload_step(job['id'])
        out.append(original)
    return "".join(out)


def compile_dn03(root: Path, *, target: str, workflow: str,
                 devtool_sha: str) -> tuple[str, dict[str, Any]]:
    if not SHA.fullmatch(devtool_sha):
        raise DN03Error("DN03 requires an immutable 40-character Devtool source SHA")
    if not SLUG.fullmatch(target) or not SLUG.fullmatch(workflow):
        raise DN03Error("DN03 requires bounded target and workflow names")
    root = root.resolve()
    if not root.is_dir() or _git(root, "rev-parse", "--is-inside-work-tree") != "true":
        raise DN03Error("DN03 requires a Git worktree")
    # .NET v1 exporter is deliberately limited to native hosted Linux amd64.
    try:
        graph = compile_graph(root, target=target, workflow=workflow,
                              devtool_sha=devtool_sha, host_profiles="ubuntu-24.04-x64",
                              cache_locks=False)
    except (ProviderError, ValueError) as exc:
        raise DN03Error("DN03 native graph refused: " + str(exc)[:240]) from None
    jobs = graph.get("jobs")
    if not isinstance(jobs, list) or not jobs or len(jobs) > 24:
        raise DN03Error("DN03 requires 1–24 reviewed native jobs")
    if any(not isinstance(j, dict) or j.get("provider") != "dotnet" or
           j.get("profile_id") != "ubuntu-24.04-x64" for j in jobs):
        raise DN03Error("DN03 requires a .NET-only Linux hosted native DAG")
    digest = graph.get("graph_sha256", "")
    if not DIGEST.fullmatch(digest) or graph.get("runtime_sha") != devtool_sha:
        raise DN03Error("DN03 graph digest or runtime source pin is invalid")
    for job in jobs:
        _host_step(job, devtool_sha, digest)
    original = workflow_yaml(graph)
    hardened = _harden(original, jobs, devtool_sha, digest)
    record = {
        "schema_version": 1, "kind": "devtool.dn03-dotnet-native-export",
        "devtool_sha": devtool_sha, "graph_sha256": digest,
        "provider_plan_sha256": graph["provider_plan_sha256"],
        # Intentionally omit consumer HEAD/tree from the durable receipt: the
        # export itself becomes a new Git commit. Provider-plan/graph digests
        # bind the reviewed config and lockfile bytes across that commit.
        "target": target, "workflow": workflow,
        "native_nodes": [j["node"] for j in jobs],
        "host_profile": "ubuntu-24.04-x64",
        "workflow_sha256": sha256(hardened.encode("utf-8")).hexdigest(),
        "generator": "DN03-R2 isolated SDK, runtime-only and RM-06 native DAG exporter",
        "runtime_archive_sha512": RUNTIME_ARCHIVE_SHA512,
        "runtime_only_legacy": any(j.get("dotnet_legacy_runtime") for j in jobs),
        "actual_host_checked": False, "remote_authenticated": False,
        "release_qualified": False,
    }
    return hardened, record


def _destinations(root: Path, target: str, workflow: str) -> tuple[Path, Path]:
    stem = f"devtool-dn03-{target}-{workflow}"
    return (_path(root, ".github/workflows/" + stem + ".yml"),
            _path(root, "docs/ci/" + stem + ".json"))


def _bytes(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, sort_keys=True, indent=2) + "\n").encode("utf-8")


def export(root: Path, *, target: str, workflow: str, devtool_sha: str,
           mode: str, confirmed: bool = False) -> dict[str, Any]:
    if mode not in ("plan", "write", "verify"):
        raise DN03Error("DN03 requires plan/write/verify mode")
    root = root.resolve()
    workflow_text, record = compile_dn03(root, target=target,
                                         workflow=workflow, devtool_sha=devtool_sha)
    yaml_out, record_out = _destinations(root, target, workflow)
    expected = ((yaml_out, workflow_text.encode("utf-8")), (record_out, _bytes(record)))
    if mode == "write":
        if not confirmed:
            raise DN03Error("DN03 writing requires explicit --yes")
        if _git(root, "status", "--porcelain", "--untracked-files=all"):
            raise DN03Error("DN03 write refuses dirty consumer Git worktree")
        if any(p.exists() for p, _ in expected):
            raise DN03Error("DN03 refuses to overwrite an existing output")
        before = _git(root, "rev-parse", "HEAD")
        created = []
        try:
            for path, payload in expected:
                path.parent.mkdir(parents=True, exist_ok=True)
                # exclusive create: do not follow a race-created symlink
                with path.open("xb") as f:
                    f.write(payload)
                created.append(path)
            if _git(root, "rev-parse", "HEAD") != before:
                raise DN03Error("DN03 source changed during write")
        except BaseException:
            for path in reversed(created):
                path.unlink(missing_ok=True)
            raise
    if mode == "verify":
        if any(not path.is_file() or path.is_symlink() or
               path.read_bytes() != payload for path, payload in expected):
            raise DN03Error("DN03 generated workflow or source receipt differs from reviewed graph")
    return {**record, "mode": mode, "wrote_files": mode == "write",
            "verified_files": mode == "verify",
            "workflow_path": str(yaml_out.relative_to(root)),
            "receipt_path": str(record_out.relative_to(root))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "write", "verify"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--devtool-sha", required=True)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = export(args.repo, target=args.target, workflow=args.workflow,
                        devtool_sha=args.devtool_sha, mode=args.mode,
                        confirmed=args.yes)
    except (DN03Error, OSError, KeyError, TypeError) as exc:
        print("DN03_REFUSED: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
