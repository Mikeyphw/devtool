#!/usr/bin/env bash
set -euo pipefail

# OCI-03 external validation gate: exercise the real controller -> SSH worker ->
# PRoot-Distro build/install -> in-image capability path.  A profile is optional;
# when omitted Devtool resolves its configured default SSH worker.
PROFILE="${1:-${DEVTOOL_OCI03_SMOKE_PROFILE:-}}"
CONTAINER="${2:-${DEVTOOL_OCI03_SMOKE_CONTAINER:-devtool-android-api36}}"
PYTHON_BIN="${DEVTOOL_PYTHON:-python3}"
REPO_ROOT="${DEVTOOL_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

mkdir -p "$HOME/.cache/devtool"
WORK="$(mktemp -d "$HOME/.cache/devtool/oci03-gate.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT HUP INT TERM
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

profile_args=()
if [ -n "$PROFILE" ]; then profile_args+=("$PROFILE"); fi

"$PYTHON_BIN" -m devtool oci doctor "${profile_args[@]}" --format json > "$WORK/doctor.json"
"$PYTHON_BIN" - "$WORK/doctor.json" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
if not p.get("ok"):
    raise SystemExit("OCI-03 smoke: worker doctor is not ready")
r=p.get("runtime") or {}
if not r.get("proot_distro") or not r.get("proot"):
    raise SystemExit("OCI-03 smoke: proot-distro/proot capability is missing")
if r.get("nested_proot"):
    raise SystemExit("OCI-03 smoke: worker is already inside nested PRoot")
PY

"$PYTHON_BIN" -m devtool oci build "${profile_args[@]}" \
  --install-as "$CONTAINER" --no-cache --format json > "$WORK/build.json"

"$PYTHON_BIN" - "$WORK/build.json" <<'PY'
import json, re, sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
if not p.get("ok"):
    raise SystemExit("OCI-03 smoke: image build did not report success")
if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(p.get("image_digest") or "")):
    raise SystemExit("OCI-03 smoke: OCI manifest digest evidence is missing")
if not re.fullmatch(r"[0-9a-f]{64}", str(p.get("image_sha256") or "")):
    raise SystemExit("OCI-03 smoke: archive SHA-256 evidence is missing")
v=p.get("validation") or {}
if not v.get("ok"):
    raise SystemExit("OCI-03 smoke: installed image capability validation failed")
c=v.get("capabilities") or {}
expected={
    "status":"ready", "architecture":"aarch64", "api":"36",
    "build_tools":"36.0.0", "ndk":"29.0.14206865",
    "ndk_host":"qemu-x86_64-wrapped-verified",
}
for k,want in expected.items():
    got=str(c.get(k) or "")
    if k == "architecture" and got == "arm64":
        continue
    if got != want:
        raise SystemExit(f"OCI-03 smoke: capability {k}={got!r}, expected {want!r}")
m=v.get("build_metadata") or {}
if m.get("definition_digest") != p.get("definition_digest"):
    raise SystemExit("OCI-03 smoke: image definition digest provenance mismatch")
if m.get("context_sha256") != p.get("context_sha256"):
    raise SystemExit("OCI-03 smoke: image context digest provenance mismatch")
if int(((m.get("toolchains") or {}).get("ndk_wrapped_host_binaries") or 0)) <= 0:
    raise SystemExit("OCI-03 smoke: NDK transparent QEMU wrapper inventory is empty")
print(json.dumps({
    "status":"ready",
    "profile":p.get("profile"),
    "container":p.get("install_as"),
    "image_digest":p.get("image_digest"),
    "archive_sha256":p.get("image_sha256"),
    "definition_digest":p.get("definition_digest"),
    "context_sha256":p.get("context_sha256"),
}, sort_keys=True))
PY
