#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "$0")" && pwd)
cache_root=${XDG_CACHE_HOME:-"$HOME/.cache"}
build=${BUILD_DIR:-"$cache_root/devtool-control-center/build"}
build_type=${BUILD_TYPE:-Release}

# ZIP archives and copied source trees can preserve timestamps that are ahead of
# the local clock. Ninja then considers build.ninja older than its configure
# inputs and repeatedly re-runs CMake. Normalize only future-dated source files;
# changing mtimes does not modify their contents or Git status.
normalize_future_mtimes() {
  local marker
  marker=$(mktemp "${TMPDIR:-/tmp}/devtool-control-center-now.XXXXXX")
  touch "$marker"
  while IFS= read -r -d '' path; do
    touch -r "$marker" "$path"
  done < <(
    find "$root" \
      -path "$root/.git" -prune -o \
      -path "$root/build" -prune -o \
      -path "$root/dist" -prune -o \
      -path "$build" -prune -o \
      -type f -newer "$marker" -print0
  )
  rm -f "$marker"
}

normalize_future_mtimes

# Never reuse a cache configured for another source tree. This commonly occurs
# when the application source is moved or replaced in-place after an update.
if [[ -f "$build/CMakeCache.txt" ]]; then
  cached_source=$(sed -n 's/^CMAKE_HOME_DIRECTORY:INTERNAL=//p' "$build/CMakeCache.txt" | tail -n 1)
  if [[ -n "$cached_source" && "$cached_source" != "$root" ]]; then
    rm -rf "$build"
  fi
fi

cmake -S "$root" -B "$build" -G Ninja -DCMAKE_BUILD_TYPE="$build_type" "$@"
cmake --build "$build" --parallel "${BUILD_JOBS:-$(nproc)}"
printf 'Built: %s\n' "$build/devtool-control-center"
