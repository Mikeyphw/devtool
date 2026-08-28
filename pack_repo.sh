#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  pack_repo.sh [options] [subdir ...]

Options:
  -o, --output PATH   Write the repository archive to PATH.
  --compression zstd|gzip
                      Compression format (default: zstd).
  --manifest PATH     Write a SHA-256 manifest for archived files.
  -h, --help          Show this help text.

Behavior:
  - With no subdir arguments, pack the whole repository.
  - With one or more subdir arguments, pack only those subtrees.
  - Respect gitignore and exclude common build/cache directories.
EOF
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
compression="zstd"
default_output="/tmp/$(basename "$repo_root")-$(date +%Y%m%d-%H%M%S).tar.zst"
output_path="$default_output"
manifest_path=""
inputs=()

while (($#)); do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -o|--output)
      if (($# < 2)); then
        echo "error: missing value for $1" >&2
        exit 1
      fi
      output_path="$2"
      shift 2
      ;;
    --compression)
      if (($# < 2)); then
        echo "error: missing value for $1" >&2
        exit 1
      fi
      compression="$2"
      shift 2
      ;;
    --manifest)
      if (($# < 2)); then
        echo "error: missing value for $1" >&2
        exit 1
      fi
      manifest_path="$2"
      shift 2
      ;;
    --output=*)
      output_path="${1#*=}"
      shift
      ;;
    --compression=*)
      compression="${1#*=}"
      shift
      ;;
    --manifest=*)
      manifest_path="${1#*=}"
      shift
      ;;
    --)
      shift
      while (($#)); do
        inputs+=("$1")
        shift
      done
      ;;
    *)
      inputs+=("$1")
      shift
      ;;
  esac
done

case "$compression" in
  zstd|gzip) ;;
  *) echo "error: unsupported compression: $compression" >&2; exit 2 ;;
esac

validate_repo_relative_input() {
  case "$1" in
    ""|.|..|/*|*"/../"*|../*|*/..|*"//"*|*'\\'*)
      echo "error: input path must be repository-relative and must not escape the repository: $1" >&2
      exit 2
      ;;
  esac
}

for input in "${inputs[@]}"; do
  validate_repo_relative_input "$input"
done

mkdir -p "$(dirname "$output_path")"

if ! command -v git >/dev/null 2>&1; then
  echo "error: git is required" >&2
  exit 1
fi

if ! command -v tar >/dev/null 2>&1; then
  echo "error: tar is required" >&2
  exit 1
fi

if [[ -z ${SOURCE_DATE_EPOCH:-} ]]; then
  SOURCE_DATE_EPOCH=$(git -C "$repo_root" log -1 --format=%ct HEAD 2>/dev/null || true)
  SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-315532800}
fi
case "$SOURCE_DATE_EPOCH" in
  *[!0-9]*|'') echo "error: SOURCE_DATE_EPOCH must be an integer" >&2; exit 2 ;;
esac
export SOURCE_DATE_EPOCH

should_skip() {
  case "$1" in
    .git|.git/*|*/.git|*/.git/*)
      return 0
      ;;
    bin|bin/*|*/bin|*/bin/*)
      return 0
      ;;
    obj|obj/*|*/obj|*/obj/*)
      return 0
      ;;
    build|build/*|*/build|*/build/*|Build|Build/*|*/Build|*/Build/*)
      return 0
      ;;
    cache|cache/*|*/cache|*/cache/*|Cache|Cache/*|*/Cache|*/Cache/*|.cache|.cache/*|*/.cache|*/.cache/*)
      return 0
      ;;
    artifacts|artifacts/*|*/artifacts|*/artifacts/*)
      return 0
      ;;
    node_modules|node_modules/*|*/node_modules|*/node_modules/*)
      return 0
      ;;
    TestResults|TestResults/*|*/TestResults|*/TestResults/*)
      return 0
      ;;
    .vs|.vs/*|*/.vs|*/.vs/*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

list_file=$(mktemp "${TMPDIR:-/tmp}/devtool-pack-list.XXXXXX")
trap 'rm -f -- "$list_file"' EXIT

{
  if ((${#inputs[@]})); then
    git -C "$repo_root" ls-files -z -co --exclude-standard -- "${inputs[@]}"
  else
    git -C "$repo_root" ls-files -z -co --exclude-standard
  fi
} | while IFS= read -r -d '' path; do
  if should_skip "$path"; then
    continue
  fi
  validate_repo_relative_input "$path"
  printf '%s\0' "$path"
done | LC_ALL=C sort -z > "$list_file"

if [[ ! -s "$list_file" ]]; then
  echo "error: no repository files matched the requested input" >&2
  exit 1
fi

tar_args=(
  -C "$repo_root"
  --null -T "$list_file"
  --sort=name
  --mtime="@${SOURCE_DATE_EPOCH}"
  --owner=0 --group=0 --numeric-owner
  --mode=go-w
  -cf "$output_path"
)
case "$compression" in
  zstd) tar_args+=(--zstd) ;;
  gzip) tar_args+=(-z) ;;
esac
tar "${tar_args[@]}"

if [[ -n "$manifest_path" ]]; then
  mkdir -p "$(dirname "$manifest_path")"
  (
    cd "$repo_root"
    while IFS= read -r -d '' path; do
      sha256sum -- "$path"
    done < "$list_file"
  ) > "$manifest_path"
fi

sha256sum "$output_path" > "$output_path.sha256"
echo "created $output_path"
echo "checksum $output_path.sha256"
[[ -z "$manifest_path" ]] || echo "manifest $manifest_path"
