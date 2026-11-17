# Building a browser with Stealth Chromium

This turns the patch series into an actual binary. Everything below is
standard Chromium building; the only step specific to Stealth Chromium is
one `tools/apply.sh` call.

## Requirements (the honest version)

| Resource | Minimum | Comfortable |
|---|---|---|
| RAM | 16 GB | 32–64 GB |
| Disk | 120 GB free | 200+ GB SSD |
| Cores | 8 | 16–32 |
| Full build time | ~4–6 h | ~1–2 h |
| Incremental after a patch edit | minutes | minutes |

Linux x64 is the easiest target (and what the vendor build targets).
macOS/Windows work too with platform-specific `install-build-deps`.

## Choosing the base

Two supported bases, both machine-checked (`tools/verify.sh` and
`tools/check-ungoogled.sh`):

1. **Vanilla Chromium at the pinned tag** (`146.0.7680.177`) — simplest,
   recommended for development and testing the fingerprint patches.
2. **ungoogled-chromium at `146.0.7680.177-1`** — what the audited vendor
   binary is actually built on (domain substitution, no Google egress).
   All Stealth Chromium patches apply on top of the ungoogled layer. This
   is the base to ship if the goal is parity with the audited build.

## Quick path (Linux, Docker)

```bash
docker build -t stealth-chromium-builder -f tools/build/Dockerfile tools/build
docker run --rm -v "$PWD:/stealth-chromium" -v ~/chromium-cache:/cache \
  stealth-chromium-builder /stealth-chromium/tools/build.sh /cache/chromium --ungoogled
# binary: ~/chromium-cache/chromium/src/out/stealth-chromium/chrome
```

## Manual path

```bash
# 1. depot_tools
git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git
export PATH="$PWD/depot_tools:$PATH"

# 2. source at the pinned tag
mkdir chromium && cd chromium
fetch --nohooks chromium   # or: gclient config + sync
cd src
git checkout 146.0.7680.177
gclient sync --with_branch_heads --with_tags -D

# 3. (optional, vendor-parity base) ungoogled-chromium
git clone https://github.com/ungoogled-software/ungoogled-chromium.git
python3 ungoogled-chromium/utils/patches.py apply src patches/series
python3 ungoogled-chromium/utils/domain_substitution.py apply ...   # see their docs

# 4. Stealth Chromium
~/path/to/stealth-chromium/tools/apply.sh .

# 5. configure + build
gn gen out/stealth-chromium --args='is_debug=false is_official_build=false symbol_level=0 dcheck_always_on=false'
autoninja -C out/stealth-chromium chrome
```

Rebuild after editing a patch: re-apply the fixed series to a clean
`git checkout .` tree (or `git am` flow per docs/REBASING.md), then
`autoninja -C out/stealth-chromium chrome` — incremental builds are fast because only
the touched translation units rebuild.

## Validating the build

```bash
python3 tools/smoke-test.py out/stealth-chromium/chrome
```

Launches the built binary headless over CDP and asserts the fingerprint
contract: persona defaults with no switches, seed determinism (two
launches with the same `--fingerprint` produce identical canvas hashes),
`--fingerprint=off` passthrough, timezone/locale/UA personas, storage
quota range, WebGL vendor/renderer strings, and mediaDevices id
stability. See the script header for the (small) Python dependencies.

## CI note

Full Chromium builds don't fit sensibly into hosted CI runners. The
repo's CI verifies patch applicability nightly (cheap); wire the actual
compile to a self-hosted runner or a beefy cloud VM using the same
Dockerfile, and publish the smoke-test results with each release tag.
