#!/usr/bin/env bash
# sync-base.sh — after generating a patch, advance the rolling diff base so
# the next patch's diff contains only its own edits. Internal tool.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
for path in "$@"; do
  mkdir -p "${ROOT}/.base/$(dirname "${path}")"
  cp "${ROOT}/.work/${path}" "${ROOT}/.base/${path}"
done
