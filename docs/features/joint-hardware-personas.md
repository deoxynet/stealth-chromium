# Joint hardware personas (0039)

## Motivation

Vendor evidence — CloakBrowser Pro CHANGELOG, release 0.4.8
(build 148.0.7778.215.5), the "biggest fingerprint update yet":

> More common, more coherent hardware identities — each `--fingerprint`
> seed is built from the most common real-world hardware values (screen,
> GPU, RAM, CPU cores, color depth, fonts, audio), chosen together so they
> form a popular, self-consistent device with no internal contradictions.

> Expanded NVIDIA GPU profiles, each matched to a plausible CPU, RAM, and
> screen combination.

Before this patch the series derived each hardware field *independently*:
0004 returned a constant 8 GiB, 0005 a constant 8 threads, 0007 one of two
per-platform screen constants, and 0016/0032 hashed the seed into the
NVIDIA model database with a GPU-only salt. Every field was deterministic,
but nothing tied them together — an identity could claim a 1366x768 panel
with 32 GiB and an RTX 4090. Anti-fingerprinting back ends score exactly
that kind of cross-field contradiction.

## Design

### Table shape

`third_party/blink/renderer/core/fingerprint/device_profiles.h`
(header-only, no BUILD.gn registration) holds three `inline constexpr`
tables, one per platform persona — `kWindowsDeviceProfiles` (18 entries),
`kMacOsDeviceProfiles` (17), `kLinuxDeviceProfiles` (14). Each
`DeviceProfile` is a complete machine:

| Field | Meaning | Value space |
|---|---|---|
| `screen_width` / `screen_height` | CSS-pixel screen size (`window.screen`, media queries) | 1366x768 … 3840x2160 |
| `color_depth` | documented persona field | 24 everywhere (see divergences) |
| `device_memory_gb` | `navigator.deviceMemory` | 8 / 16 / 32 |
| `hardware_concurrency` | `navigator.hardwareConcurrency` (logical cores) | 8 / 10 / 12 / 16 |
| `nvidia_device_id` | PCI id into the shared `kNvidiaGpuModels` database (0016 `.cc` table / 0032 header table, identical entries) | `0x2507` … `0x2C05`; 0 on the macOS persona |
| `apple_soc` | macOS-persona SoC name in the 0016 renderer-string style (`"M2 Pro"` …) | `""` elsewhere |

GPU *selection* lives in the profile; GPU *strings and limits* do not —
the WebGL renderer/vendor dressing, the WebGPU architecture bucket table
and limit clamps are reused verbatim from 0016/0032. Because PCI ids are
unique across the model table, the profile id resolves exactly one entry
in both copies of the database, so WebGL `UNMASKED_RENDERER_WEBGL`,
WebGPU `adapter.info`, RAM, cores and screen can never disagree for one
identity.

### Selection

```
index = Mix(HashFor("device-profile") ^ Hash(platform_key)) % table_size
```

`platform_key` is `"windows"` / `"macos"` / `"linux"` from
`fingerprint::SpoofedPlatform()`, defaulting to the Linux tables when the
platform switch is absent or unrecognized — the same default the GPU
personas (0016/0032) apply. The selection is cached in a function-local
static (command line is immutable by the time renderer code runs). Same
seed + same platform ⇒ byte-identical hardware across launches, always.

### Rewire sites and precedence

Only the persona-default branch of each ladder changed; every consumer
keeps the series getter-ladder contract **explicit `--fingerprint-*`
switch > profile > stock**, and with `--fingerprint=off` the profile
pointer is null and no profile code executes:

- `core/frame/navigator_device_memory.cc` — RAM default was `8.0f`, now
  `profile->device_memory_gb`.
- `core/frame/navigator_concurrent_hardware.cc` — core count default was
  `8u`, now `profile->hardware_concurrency`.
- `core/fingerprint/screen_dimensions.h` — per-platform constants
  (1440x900 / 1920x1080) replaced by `profile->screen_width/height`;
  the 0007 gate is unchanged (screen defaults still require *both*
  `--fingerprint` and `--fingerprint-platform`; RAM/cores/GPU follow the
  seed alone, as in 0004/0005/0016).
- `modules/webgl/gpu_fingerprint.cc` — the seed path no longer hashes
  `"gpu_renderer"` into the model table; the profile's PCI id (or Apple
  SoC) selects the model. `--fingerprint-gpu-renderer` / `--fingerprint-gpu-vendor`
  still win untouched.
- `modules/webgpu/webgpu_fingerprint.h` — `ResolveNvidiaModelIndex()`
  seed path resolves the same profile id; the explicit-renderer substring
  match is unchanged.

## Why joint sampling (persona-space rationale)

Independent per-field hashing samples a *product space*: 3 screens x 4 RAM
values x 4 core counts x 57 GPU models — millions of combinations, most of
which correspond to no shipped machine. Joint sampling collapses the
hardware persona to the ~49 real machines in the tables. The trade is
deliberate: the hardware-fingerprint entropy drops to ~4.1 bits (log2 of
the per-platform table size), but every reachable persona is a popular,
mass-produced device — the identity crowd each persona hides in is far
larger than the fake "unique" combos it replaces. This matches the
vendor's framing ("most common real-world values ... no internal
contradictions") and is the same trade the vendor made in 0.4.8.

## Divergences from the vendor build

No vendor binary ground truth exists for this feature at the series base
(146.0.7680.177); it shipped in the Pro-only 148.0.7778.215.5 build and is
reconstructed here from the CHANGELOG prose. Known divergences:

- **Table contents are ours.** Profile counts (18/17/14) and the exact
  screen/RAM/core/GPU pairings follow public hardware-popularity data
  (Steam-survey-style distributions, Apple tech specs), not a recovered
  vendor table. Semantics — one joint selection per (seed, platform) —
  are believed to match.
- **Windows/Linux GPU pool is NVIDIA-only.** The spec prose mentions
  GTX 1650 / UHD 630 / Radeon RX 580 class pairings, but the 0016/0032
  machinery (vendor strings, renderer dressing, architecture buckets) is
  NVIDIA-shaped and the patch mandate was to reuse it without duplicating
  GPU strings. AMD/Intel pairings would need new vendor/renderer string
  machinery and are deferred; GTX 1650 is absent from the shared database,
  so the RTX 3050 plays the equivalent budget-desk role.
- **4 GiB RAM never appears.** The allowed bucket set includes 4, but no
  plausible pairing exists with the RTX-class GPU pool the strings can
  express; 8 GiB is the floor, matching the real bottom of the
  discrete-GPU market.
- **Mac RAM is bucketed to 8/16/32.** Real 18/24 GB Apple configs map to
  the 16 bucket — the same round-down Chrome's own `deviceMemory`
  bucketing performs. Core counts use the *logical* CPU counts those SoCs
  report (M2 Pro 10 or 12, M3 Pro 11/12, M4 10 ...).
- **Color depth is a documented constant (24), not wired.** Stock Chrome
  returns 24 unconditionally from `Screen::colorDepth()` (per CSSOM's
  privacy recommendation), so there is nothing to spoof; the field exists
  because the vendor prose lists color depth as part of the joint
  persona. A 30-bit host display still reports real depth — accepted
  residual, identical to stock.
- **Screen scale/DPR hint omitted** — no 0007/0016 consumer reads one.
- **`--fingerprint-platform=darwin` is normalized to `macos`** in the two
  GPU platform resolvers (`gpu_fingerprint.cc`,
  `webgpu_fingerprint.h`), matching the alias `SpoofedPlatform()` already
  accepts, so the profile's Apple SoC can never be dressed in NVIDIA
  vendor strings.
- **0016's activation gate now requires `fingerprint::Enabled()`** (an
  explicit off-value disables GPU strings/limits/explicit GPU switches
  too). Previously the mere presence of `--fingerprint` activated the GPU
  persona even for `--fingerprint=off`, contradicting the series-wide
  off semantics; fixing it was required for "off ⇒ completely stock" at
  the rewired sites.

## Extending the table

Add a row to the relevant platform table — nothing else. Constraints:

1. The GPU column must reference an id that exists in *both* copies of
   `kNvidiaGpuModels` (ids are unique, so lookup is order-independent),
   or, on the macOS table, an SoC name the 0016 renderer dressing can
   interpolate.
2. The combination must be a machine people actually own: screen the
   panel ships with, RAM bucket the SKU sells with, logical core count
   the CPU reports, GPU the chassis thermal budget allows.
3. No other code changes: selection is `mod table_size`, consumers read
   fields, and both tables and consumers stay header-only.

Regenerate expectations after table edits: per-identity hardware values
shift for *every* seed (the modulo space changed) — bump anything that
pins expected personas in launcher tooling.
