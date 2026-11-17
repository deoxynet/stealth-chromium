# Stealth Chromium — an open-source Chromium anti-fingerprinting patch series

Stealth Chromium patches the browser itself to control its fingerprint.
Its maintainable, per-surface patch series spoofs or noises the browser-fingerprint
surfaces commonly read by tracking and bot-detection scripts: canvas, text
metrics, client rects, audio,
screen dimensions, hardware reporting, user agent + UA-CH, timezone,
locale/Accept-Language, WebGL/GPU, storage quota, and more.

Every patch is driven by a single 64-bit **identity seed** (`--fingerprint`,
decimal). Same seed ⇒ same fingerprint everywhere, every launch. No seed ⇒
the browser mints a random one at startup, so every session is a fresh,
coherent identity. `--fingerprint=off` disables the entire system and every
patch degrades to stock Chromium behavior.


See [docs/BUILDING.md](docs/BUILDING.md) to actually compile the browser
(Dockerfile + end-to-end build script + post-build fingerprint smoke
test).

**Status: all 46 patches implemented and machine-verified against
Chromium `146.0.7680.177`** (verify.sh: 46/46; patches 0038 and 0046 touch
V8 / SwiftShader sources, resolved via the DEPS-pinned revisions) (every surface in the spec catalog; remaining
approximations are listed in
[docs/patch-status.md](docs/patch-status.md) "Open items").


## Using the series

```bash
# against a full Chromium checkout (git):
tools/apply.sh /path/to/chromium/src

# or with git-am semantics (one commit per patch):
git -C /path/to/chromium/src am ~/stealth-chromium/series/*.patch
```

Then build Chromium normally. Fingerprinting is on by default (random
identity per launch). Control it with switches:

| Switch | Effect |
|---|---|
| `--fingerprint=<decimal u64>` | Pin a deterministic identity |
| `--fingerprint=off` | Disable all spoofing (stock behavior) |
| `--fingerprint-noise=false` | Disable noise (canvas/text/rects/audio), keep persona values |
| `--fingerprint-platform=windows\|macos\|linux` | Desktop OS persona (UA, platform, screen defaults, GPU pool, timezone defaults) |
| `--fingerprint-gpu-vendor`, `--fingerprint-gpu-renderer` | Override GPU strings (else seed-derived from the NVIDIA model DB) |
| `--fingerprint-screen-width/height`, `--fingerprint-taskbar-height` | Screen dimensions (else 1920×1080 / 1440×900 persona defaults) |
| `--fingerprint-device-memory`, `--fingerprint-hardware-concurrency` | Hardware reporting |
| `--fingerprint-locale`, `--fingerprint-timezone` | Locale/Accept-Language and IANA timezone |
| `--fingerprint-webrtc-ip=<ip>` | WebRTC public-IP spoof (candidates/SDP/stats); also enables DNS-timing normalization and re-enables nonproxied UDP gathering. Unset = stock WebRTC behavior |
| `--fingerprint-fonts-dir=<dir>` | Extra fonts counted as available for the font persona |
| `--fingerprint-storage-quota=<MiB>` | navigator.storage quota (else seed-derived 400–600 MiB) |
| `--fingerprint-brand`, `--fingerprint-brand-version`, `--fingerprint-platform-version` | UA-CH persona details |
| `--fingerprint-report-version=<major.minor.build.patch>` | Reported Chrome version for UA/UA-CH/chrome://version/`--version`; default = current stable constant, updated at rebase time |

All switches are propagated to renderer processes automatically (patch
0003).

## Repo layout

```
series/     the patch series, in apply order (git-apply/git-am compatible)
docs/       architecture, rebasing, building, per-patch status manifest
tools/      apply.sh, verify.sh, check-ungoogled.sh, build.sh,
            smoke-test.py, fetch-upstream.sh (+ internal helpers)
tools/build Dockerfile for a full-browser build environment
.github/    CI: nightly verify against fresh Chromium tags
```

## Verification & rebasing

- `tools/verify.sh` — apply the series against a tag (default: the pinned
  base) using a sparse file mirror. See the script header.
- `docs/REBASING.md` — the workflow for moving the series to a new Chromium
  version (the intended maintenance loop for this repo).
- `docs/patch-status.md` — every fingerprint surface, its spec document,
  and implementation status.

## Attribution & license

- The series is licensed under **GPL-3.0** ([LICENSE](LICENSE)).
