#!/usr/bin/env bash
# fetch-upstream.sh — materialize the upstream Chromium files touched by the
# patch series into a local mirror directory, at a given tag/commit.
#
# Usage:
#   tools/fetch-upstream.sh <tag-or-ref> [mirror-dir]
#
# The mirror is a sparse, read-only copy of upstream used by verify.sh to
# dry-run the series without a full Chromium checkout (which is ~100GB).
# Files are downloaded from chromium.googlesource.com's Gitiles ?format=TEXT
# endpoint (base64 blob), which serves any path at any ref.
set -euo pipefail

# Sanitize a ref (tags/branches/slashes) into a single path component.
__stealth_chromium_sanitize_ref() {
  printf '%s' "$1" | tr '/.' '__'
}

REF="${1:?usage: fetch-upstream.sh <tag-or-ref> [mirror-dir]}"
MIRROR="${2:-.upstream-mirror/$(__stealth_chromium_sanitize_ref "${REF}")}"
SERIES_DIR="$(cd "$(dirname "$0")/../series" && pwd)"
BASE_URL="https://chromium.googlesource.com/chromium/src/+/${REF}"

mkdir -p "${MIRROR}"

# Collect every path modified (not added) by the series. Paths whose diff
# header pair is `--- /dev/null` / `+++ b/...` are files the series creates;
# they have no upstream counterpart and are skipped — including when a later
# patch modifies them again (a `--- a/...` hunk against a series-created
# file must not be fetched from upstream).
paths="$(awk '
  /^--- /  { devnull = ($2 == "/dev/null"); next }
  /^\+\+\+ / {
    path = $2; sub(/^b\//, "", path); sub(/\t.*/, "", path)
    if (devnull) {
      created[path] = 1
    } else if ($2 != "/dev/null") {
      modified[path] = 1
    }
    devnull = 0; next
  }
  END {
    for (path in modified) {
      if (!(path in created)) {
        print path
      }
    }
  }
' "${SERIES_DIR}"/*.patch 2>/dev/null | sort -u || true)"

if [[ -z "${paths}" ]]; then
  echo "no file paths found in ${SERIES_DIR}" >&2
  exit 1
fi

# Paths under v8/ live in the separate V8 repository, checked out at the
# revision this Chromium ref pins in its DEPS ("v8_revision"). Resolve that
# once per ref and serve those files from the V8 mirror instead.
__stealth_chromium_v8_revision() {
  local cache="${MIRROR}/.v8_revision"
  if [[ -s "${cache}" ]]; then
    cat "${cache}"
    return
  fi
  local rev
  rev="$(curl -sf "${BASE_URL}/DEPS?format=TEXT" | base64 -d |
    sed -n "s/.*'v8_revision': '\([0-9a-f]\{40\}\)'.*/\1/p" | head -1)"
  if [[ -z "${rev}" ]]; then
    echo "" >&2
    return
  fi
  printf '%s' "${rev}" > "${cache}"
  printf '%s' "${rev}"
}

count=0
fail=0
while IFS= read -r rel; do
  [[ -z "${rel}" ]] && continue
  dest="${MIRROR}/${rel}"
  [[ -s "${dest}" ]] && { count=$((count + 1)); continue; }  # cached
  mkdir -p "$(dirname "${dest}")"
  url="${BASE_URL}/${rel}"
  if [[ "${rel}" == v8/* ]]; then
    if [[ -z "${V8_REV:-}" ]]; then
      V8_REV="$(__stealth_chromium_v8_revision)"
      if [[ -z "${V8_REV}" ]]; then
        echo "MISSING v8_revision in DEPS for ${REF}" >&2
        fail=$((fail + 1))
        continue
      fi
      echo "# v8/ paths resolved to V8 @ ${V8_REV}" >&2
    fi
    url="https://chromium.googlesource.com/v8/v8/+/${V8_REV}/${rel#v8/}"
  fi
  if curl -sf "${url}?format=TEXT" | base64 -d > "${dest}"; then
    count=$((count + 1))
  else
    echo "MISSING upstream: ${REF}:${rel}" >&2
    rm -f "${dest}"
    fail=$((fail + 1))
  fi
done <<< "${paths}"

echo "fetched/cached ${count} files into ${MIRROR} (${fail} missing) at ${REF}"
[[ "${fail}" -eq 0 ]]
