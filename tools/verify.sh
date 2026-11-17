#!/usr/bin/env bash
# verify.sh — dry-run the entire patch series against a Chromium ref.
#
# Usage:
#   tools/verify.sh [tag-or-ref]     # default: the pinned base tag
#
# Fetches only the upstream files the series touches (a few hundred KB,
# not a 100GB checkout) into .upstream-mirror/, then applies every
# series/*.patch in order into a scratch copy and reports a per-patch
# pass/fail table. Use a different tag to preview how much rework a
# Chromium version bump needs:
#
#   tools/verify.sh 147.0.XXXX.YYY
set -euo pipefail

BASE_TAG="146.0.7680.177"
REF="${1:-${BASE_TAG}}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERIES_DIR="${ROOT}/series"
# (mirror path is derived per-ref below)
CHECK="$(mktemp -d "${ROOT}/.verify-tree.XXXXXX")"
trap 'rm -rf "${CHECK}"' EXIT

echo "# verifying fp-patches series against ${REF}"
# Per-ref mirror (default inside fetch-upstream.sh): each tested ref gets
# its own pristine file cache, never mixed across versions.
MIRROR="${ROOT}/.upstream-mirror/$(printf '%s' "${REF}" | tr '/.' '__')"
"${ROOT}/tools/fetch-upstream.sh" "${REF}" "${MIRROR}" >/dev/null

cp -R "${MIRROR}/." "${CHECK}/"

pass=0; fail=0; failed=()
printf "%-8s %-55s %s\n" "RESULT" "PATCH" "NOTE"
for p in "${SERIES_DIR}"/*.patch; do
  name="$(basename "${p}")"
  note=""
  if out="$(git -C "${CHECK}" apply --whitespace=nowarn "${p}" 2>&1)"; then
    printf "%-8s %-55s %s\n" "PASS" "${name}" "${note}"
    pass=$((pass + 1))
  else
    printf "%-8s %-55s %s\n" "FAIL" "${name}" "$(echo "${out}" | head -1)"
    fail=$((fail + 1)); failed+=("${name}")
  fi
done

echo
echo "# ${pass} passed, ${fail} failed (base ${BASE_TAG}, tested ${REF})"
if [[ "${fail}" -gt 0 ]]; then
  printf '# failing: %s\n' "${failed[*]}"
  exit 1
fi
