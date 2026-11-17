#!/usr/bin/env bash
# build.sh — full browser build with Stealth Chromium applied.
#
# Usage (inside the Docker image from tools/build/Dockerfile, or on a
# machine with depot_tools + build deps installed):
#   tools/build.sh <work-dir> [--ungoogled] [--tag 146.0.7680.177]
#
# <work-dir>  where the Chromium checkout lives (created if absent;
#             reuse the same dir for fast incremental rebuilds)
# --ungoogled base on ungoogled-chromium (vendor-parity) instead of
#             vanilla Chromium
set -euo pipefail

WORK="${1:?usage: build.sh <work-dir> [--ungoogled] [--tag <tag>]}"
shift || true
BASE="vanilla"
TAG="146.0.7680.177"
UNGOOGLED_TAG="${TAG}-1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ungoogled) BASE="ungoogled";;
    --tag) TAG="$2"; UNGOOGLED_TAG="${2}-1"; shift;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
  shift
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${WORK}/src"
mkdir -p "${WORK}"

if ! command -v fetch >/dev/null && [[ ! -d "${WORK}/depot_tools" ]]; then
  git clone --depth=1 https://chromium.googlesource.com/chromium/tools/depot_tools.git "${WORK}/depot_tools"
fi
export PATH="${WORK}/depot_tools:${PATH}"

# --- fetch / sync the source tree at the pinned tag -------------------------
if [[ ! -d "${SRC}" ]]; then
  (cd "${WORK}" && fetch --nohooks chromium)
fi
(cd "${SRC}" \
  && git fetch --tags origin \
  && git checkout "${TAG}" \
  && gclient sync -D --with_branch_heads --with_tags --no-history)

# --- ungoogled base (optional) ----------------------------------------------
if [[ "${BASE}" == "ungoogled" ]]; then
  if [[ ! -d "${WORK}/ungoogled-chromium" ]]; then
    git clone --depth=1 --branch "${UNGOOGLED_TAG}" \
      https://github.com/ungoogled-software/ungoogled-chromium.git \
      "${WORK}/ungoogled-chromium"
  fi
  (cd "${WORK}/ungoogled-chromium" \
    && python3 utils/prune_patches.py . \
    && python3 utils/patches.py apply "${SRC}" patches/series \
    && python3 utils/domain_substitution.py apply -r domain_regex.list \
         -f domain_substitution.list "${SRC}")
fi

# --- Stealth Chromium --------------------------------------------------------------
"${ROOT}/tools/apply.sh" "${SRC}"

# --- configure + build --------------------------------------------------------
(cd "${SRC}" \
  && gn gen out/stealth-chromium --args="
      is_debug=false
      symbol_level=0
      dcheck_always_on=false
      treat_warnings_as_errors=false" \
  && autoninja -C out/stealth-chromium chrome)

echo
echo "build complete: ${SRC}/out/stealth-chromium/chrome"
echo "validate:        python3 ${ROOT}/tools/smoke-test.py ${SRC}/out/stealth-chromium/chrome"
