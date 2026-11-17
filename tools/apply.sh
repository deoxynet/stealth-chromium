#!/usr/bin/env bash
# apply.sh — apply the Stealth Chromium series to a Chromium checkout.
#
# Usage:
#   tools/apply.sh /path/to/chromium/src [--keep-going]
#
# Applies every series/*.patch in order with `git apply`. Without
# --keep-going, the first failure aborts (recommended when rebasing: fix the
# reported patch, re-run). With --keep-going, all applicable patches are
# applied and failures are listed at the end.
set -uo pipefail

CHECKOUT="${1:?usage: apply.sh /path/to/chromium/src [--keep-going]}"
KEEP_GOING="${2:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERIES_DIR="${ROOT}/series"

if [[ ! -d "${CHECKOUT}/.git" ]]; then
  echo "error: ${CHECKOUT} is not a git checkout of Chromium" >&2
  exit 1
fi

fail=0
for p in "${SERIES_DIR}"/*.patch; do
  name="$(basename "${p}")"
  if git -C "${CHECKOUT}" apply --whitespace=nowarn "${p}"; then
    echo "PASS  ${name}"
  else
    echo "FAIL  ${name}"
    fail=$((fail + 1))
    [[ "${KEEP_GOING}" == "--keep-going" ]] || { echo "aborted (use --keep-going to skip failures)" >&2; exit 1; }
  fi
done

if [[ "${fail}" -gt 0 ]]; then
  echo "${fail} patch(es) failed to apply" >&2
  exit 1
fi
echo "series applied cleanly"
