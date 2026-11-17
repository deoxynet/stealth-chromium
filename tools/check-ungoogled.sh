#!/usr/bin/env bash
# check-ungoogled.sh — can fp-patches stack on an ungoogled-chromium tree?
#
# The vendor binary audited by the reversing workspace is
# ungoogled-chromium + fingerprint patches. This simulates that stack on
# the sparse mirror: each ungoogled patch (in series order) is applied
# restricted to the files present in the mirror (--include), then the
# fp-patches series is applied on top. Reports per-layer results.
#
# Usage:
#   tools/check-ungoogled.sh <ungoogled-patches-dir> [tag]
#
#   <ungoogled-patches-dir> = a checkout/archive of
#   github.com/ungoogled-software/ungoogled-chromium at the matching tag
#   (e.g. 146.0.7680.177-1)
set -euo pipefail

UNGOOGLED="$(cd "${1:?usage: check-ungoogled.sh <ungoogled-patches-dir> [tag]}" && pwd)"
TAG="${2:-146.0.7680.177}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MIRROR="${ROOT}/.upstream-mirror/$(printf '%s' "${TAG}" | tr '/.' '__')"
CHECK="$(mktemp -d "${ROOT}/.verify-tree.XXXXXX")"
trap 'rm -rf "${CHECK}"' EXIT

cp -R "${MIRROR}/." "${CHECK}/"

echo "# layer 1: ungoogled-chromium patches (${UNGOOGLED}/series)"
ug_applied=0; ug_relevant=0; ug_fail=0; ug_failed=()
while IFS= read -r rel; do
  [[ -z "${rel}" || "${rel}" == \#* ]] && continue
  p="${UNGOOGLED}/${rel}"
  [[ -f "${p}" ]] || { echo "MISSING ${rel}" >&2; continue; }
  # Restrict each patch to files present in the sparse mirror.
  includes=()
  while IFS= read -r f; do
    [[ -n "${f}" ]] && includes+=(--include="${f}")
  done < <(awk '/^--- /{print $2}' "${p}" | sed -e 's|^a/||' -e 's|\t.*||' | \
      grep -v '^/dev/null$' | while IFS= read -r f; do
        [[ -f "${CHECK}/${f}" ]] && echo "${f}"
      done)
  if [[ ${#includes[@]} -eq 0 ]]; then
    continue  # patch touches nothing in our surface
  fi
  ug_relevant=$((ug_relevant + 1))
  if git -C "${CHECK}" apply --whitespace=nowarn "${includes[@]}" "${p}" 2>/dev/null; then
    ug_applied=$((ug_applied + 1))
  else
    ug_fail=$((ug_fail + 1)); ug_failed+=("${rel}")
  fi
done < "${UNGOOGLED}/series"
echo "# ungoogled: ${ug_relevant} patches touch the fp-patches surface; ${ug_applied} applied, ${ug_fail} failed"
[[ "${ug_fail}" -gt 0 ]] && printf '# ungoogled failed: %s\n' "${ug_failed[*]}"

echo
echo "# layer 2: fp-patches series on top"
pass=0; fail=0; failed=()
for p in "${ROOT}"/series/*.patch; do
  name="$(basename "${p}")"
  if git -C "${CHECK}" apply --whitespace=nowarn "${p}" 2>/dev/null; then
    pass=$((pass + 1)); printf "PASS  %s\n" "${name}"
  else
    fail=$((fail + 1)); failed+=("${name}"); printf "FAIL  %s\n" "${name}"
  fi
done
echo
echo "# fp-patches on ungoogled base: ${pass} passed, ${fail} failed"
[[ "${fail}" -gt 0 ]] && printf '# failing: %s\n' "${failed[*]}"
exit $((fail > 0 ? 1 : 0))
