#!/usr/bin/env bash
# mkpatch.sh — generate a git-am/git-apply compatible patch file from a work
# tree diffed against the upstream mirror.
#
# Usage:
#   tools/mkpatch.sh <out.patch> <number> <subject> <spec-doc> <path>...
#
# Expects two trees:
#   .upstream-mirror/  pristine upstream files (fetch-upstream.sh)
#   .work/             upstream files with our edits applied (or new files)
#
# For each <path>: if .work/<path> exists and .upstream-mirror/<path> does not,
# the file is treated as newly added. If only the mirror copy exists, the file
# is treated as deleted. Otherwise a normal modification diff is emitted.
set -euo pipefail

OUT="${1:?}"; NUM="${2:?}"; SUBJECT="${3:?}"; SPEC="${4:?}"; shift 4
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MIRROR="${STEALTH_CHROMIUM_PATCH_BASE:-${ROOT}/.upstream-mirror}"
WORK="${ROOT}/.work"

body="$(mktemp)"
trap 'rm -f "${body}"' EXIT

for path in "$@"; do
  m="${MIRROR}/${path}"
  w="${WORK}/${path}"
  if [[ -f "${w}" && ! -f "${m}" ]]; then
    diff -u -L "/dev/null" -L "b/${path}" /dev/null "${w}" >> "${body}" || true
  elif [[ -f "${m}" && ! -f "${w}" ]]; then
    diff -u -L "a/${path}" -L "/dev/null" "${m}" /dev/null >> "${body}" || true
  else
    diff -u -L "a/${path}" -L "b/${path}" "${m}" "${w}" >> "${body}" || true
  fi
done

if [[ ! -s "${body}" ]]; then
  echo "mkpatch: empty diff for ${OUT}" >&2
  exit 1
fi

{
  echo "From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001"
  echo "From: Stealth Chromium contributors"
  echo "Date: Thu, 1 Jan 2026 00:00:00 +0000"
  echo "Subject: [PATCH ${NUM}] ${SUBJECT}"
  echo "X-Spec: ${SPEC}"
  echo "X-Files: $*"
  echo ""
  echo "---"
  echo ""
  cat "${body}"
} > "${OUT}"
echo "wrote ${OUT} ($(grep -c '^@@' "${OUT}") hunks)"
