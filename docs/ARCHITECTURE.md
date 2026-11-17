# fp-patches architecture

## The identity seed

One decimal-encoded u64 (`--fingerprint`) drives everything deterministic.
Patch 0002 mints a random seed in the browser process when none is passed;
patch 0003 propagates every fingerprint switch to renderer processes via
the standard `kSwitchNames` table. No Mojo, no prefs, no IPC beyond the
existing command-line plumbing — same design as the vendor build, because
it keeps every consumer trivially testable from a plain process launch.

Explicit off-values (`off`/`false`/`0`/`disable`/`disabled`) disable the
whole system; every helper then reports "no spoof" and every call site
falls back to stock behavior.

## Core (patch 0001)

`third_party/blink/public/common/fingerprint/fingerprint.h` — header-only,
no BUILD.gn registration needed:

- `Enabled()`, `Seed()`, `SeedString()`, `NoiseEnabled()`,
  `SpoofedPlatform()` — switch access with the off-value semantics above.
- `Hash` (FNV-1a 64), `Mix` (splitmix64 finalizer), `HashFor(purpose)`,
  `RandomFor(purpose)`, `RandomBelow` — stable deterministic derivation.
  Deliberately *not* `std::hash` (libc++ internals, unstable across
  builds): same seed ⇒ same values on every platform, which matters more
  than bit-parity with any other build.
- `NoiseLcg` — the vendor's noise-family LCG constants
  (`0x17385CA9`/`0x47502932`), for pixel/sample streams.
- `SwitchUint`/`SwitchDouble` — uniform per-surface override parsing.

Switch name constants live in `third_party/blink/public/common/switches.h`
(`blink::switches`) because that header is already includable from blink
renderer code, content, and chrome — one definition, no duplication.

Renderer-side composite ladders that need small state (screen dimensions,
client-rects offsets, quota persona) live in tiny headers under
`third_party/blink/renderer/core/fingerprint/` or next to their consumers.

## Patch taxonomy

Each series patch is exactly one fingerprint surface:

| Kind | Example | Content |
|---|---|---|
| Value spoof | 0004–0007, 0010–0011 | Override a getter; switch → persona default → stock |
| Readback noise | 0012–0015 | Deterministic perturbation of data returned to JS |
| Pool persona | 0016–0017 | Seed-indexed selection from an embedded database |

Two patterns dominate:

**Getter ladder** (value spoofs):

```cpp
if (auto v = fingerprint::SwitchUint(switches::kFingerprintThing))
  return *v;                       // explicit override wins
if (fingerprint::Seed())
  return PersonaDefault();         // seed persona
return StockValue();               // no fingerprinting at all
```

**Readback noise gate**:

```cpp
if (fingerprint::NoiseEnabled()) {
  Noise(data, length);             // deterministic per (seed, position)
}
```

`NoiseEnabled()` = seed present AND `--fingerprint-noise != "false"`. That
single gate replaces the vendor's per-site inlined checks and the legacy
Bromite runtime-feature wiring (`runtime_enabled_features.json5`,
`about:flags`, `runtime_features.cc`): fewer files touched, fewer rebases.
The legacy Bromite switch names are still declared (0001) so launchers
written against them keep working; they are inert.

## Consistency guarantees (what the series preserves)

- JS and CSS see the same spoofed screen size (0007 hooks both
  `window.screen` and media queries).
- `navigator.userAgent`, UA-CH headers, and `navigator.userAgentData`
  derive from one persona function (0010, `embedder_support`), so the
  classic UA and client hints cannot disagree.
- Accept-Language header, `navigator.languages`, and renderer ICU locale
  come from the same switch (0009).
- Quota is never reported below usage (0017).
- WebGL limits (0006-era constants, 0016) and the GPU strings come from
  the same seed, so the persona is internally coherent.

## Known deliberate divergences from the vendor build

Documented per patch in `X-Spec`/commit bodies; the major ones:

- Stable own hash instead of libc++ `std::hash` (values differ, semantics
  identical).
- Noise gates unified on `fingerprint::NoiseEnabled()` instead of
  duplicated inline checks + runtime features (0001 note).
- `GetUserAgentMetadata`/`GetUserAgent` are patched centrally in
  `components/embedder_support` rather than at three blink call sites —
  at 146 all three sites are fed from that one function.
- The vendor's WebGL debug-shader HLSL synthesizer (a ~12 KB text
  generator observed in the binary) is reimplemented in simplified form
  (0034): a deterministic generator that emits every observed marker but
  is not vendor-bit-identical — the original was recovered only at
  string level.
- `--fingerprint=off` (ours) vs the vendor's wrapper-side switch stripping:
  same observable effect, simpler plumbing.
