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

configure_args=(-G Ninja -DCMAKE_BUILD_TYPE="$build_type")
if command -v ccache >/dev/null 2>&1 && [[ -z ${CMAKE_CXX_COMPILER_LAUNCHER:-} ]]; then
  configure_args+=( -DCMAKE_CXX_COMPILER_LAUNCHER=ccache )
fi

needs_configure=1
if [[ -f "$build/CMakeCache.txt" && -f "$build/build.ninja" && $# -eq 0 ]]; then
  cached_type=$(sed -n 's/^CMAKE_BUILD_TYPE:STRING=//p' "$build/CMakeCache.txt" | tail -n 1)
  if [[ "$cached_type" == "$build_type" ]]; then
    needs_configure=0
  fi
fi

if (( needs_configure )); then
  cmake -S "$root" -B "$build" "${configure_args[@]}" "$@"
fi

# Ninja automatically regenerates its files when CMake inputs changed, so a
# warm build can skip the explicit configure pass without missing updates.
cmake --build "$build" --parallel "${BUILD_JOBS:-$(nproc)}"
printf 'Built: %s\n' "$build/devtool-control-center"
