# Stealth Chromium architecture

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

### Audio readback boundary and limit

Patch 0015 applies the once-per-channel noise in the JavaScript-facing
`getChannelData(unsigned, ExceptionState&)` overload and in `copyFromChannel`.
The internal `getChannelData(unsigned)` overload stays stock: offline rendering
uses it to share an uninitialized buffer before writing the rendered samples.
Applying noise there would consume the guard too early, then have the renderer
overwrite the noise. `tools/test-audio-noise.py` compiles those extracted
accessor bodies with small adapters and exercises that sequence; it does not
replace a full Blink build or the post-build smoke test.

The additive offset depends on seed and sample index, with no signal, buffer,
channel or origin separation. A known-zero buffer therefore reveals the same
offset for subtraction. Fixing the readback boundary does not resolve that
calibration weakness, and changing an exact fingerprint sum is not a claim of
fingerprinting resistance.

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

## Pro-build feature patches (0039–0042)

Four patches reconstructed from the vendor's *Pro* CHANGELOG prose (no
binary ground truth — the free v146 build predates them). Design notes and
divergences: [docs/features/](features/).

- **0039 joint hardware personas** — device-profile tables per platform
  (18 Windows / 17 macOS / 14 Linux complete profiles). The seed + platform
  selects one profile; the persona-default branches of deviceMemory,
  hardwareConcurrency, screen, GPU model, and WebGPU adapter read from it,
  so hardware values are jointly sampled instead of independent per-field
  hashes. Explicit switches still win; no new switch.
- **0040 reported-version override** — one source of truth at
  `base/version_info` `GetVersionNumber()` (de-constexpr-ized): explicit
  `--fingerprint-report-version` > rebase-time stable constant >
  (fingerprint off) real build version. Feeds the UA token, UA-CH
  brands/fullVersionList, `chrome://version`, and `--version`. Bump the
  constant at each rebase (see REBASING.md).
- **0041 CDP UA-override coherence** — a bare
  `Emulation.setUserAgentOverride` derives a full UserAgentMetadata
  (`cdp_ua_override.h` parser), which is pushed through the WebContents
  override so every frame (OOPIFs included) agrees with the top frame;
  the persona fills fields the override doesn't imply. Gated on
  `fingerprint::Enabled()`; no new switch.
- **0042 host-font isolation** — `FontCache::GetFontPlatformData` fails
  closed to the persona inventory (persona lists + `--fingerprint-fonts-dir`
  + generic families), closing the `document.fonts.check` /
  measureText host-font oracle. Applies to same-platform personas too.
- **0043 phantom Widevine presence** — a no-CDM build rejects
  `com.widevine.alpha`, contradicting any Chrome persona. When
  fingerprinting is on and no real CDM is registered, a phantom key-system
  entry (`media/cdm/phantom_widevine.h`) is injected at
  `KeySystemsImpl::OnSupportedKeySystemsUpdated` and `MojoCdmFactory`
  builds a local stub CDM (the AesDecryptor pattern), so the full
  network-free probe ladder — support query, `getConfiguration()`,
  `createMediaKeys()`, `generateRequest()` (which resolves then stalls
  like a CDM awaiting its license server) — matches desktop Chrome with
  an L3 CDM. A real side-loaded CDM wins whenever present; fingerprint
  off ⇒ stock rejections.
- **0044 onerror source consistency** — stock Chromium leaks `""` as
  `ErrorEvent.filename` for string-compiled timer scripts under a CDP
  `Runtime.enable` session (no session falls back to the document URL).
  In `CaptureSourceLocation`'s stack-trace early-return path, an empty
  top-frame URL now falls through to the document-URL resolution —
  gated on fingerprinting; off ⇒ the stock artifact. Workers (blob:)
  and `new Error().stack` untouched.
- **0045 pointer/hover persona** — the four `MediaValues::Calculate*`
  statics (the same choke level 0007 uses for screen size) report a
  desktop-with-mouse answer: `(pointer|any-pointer): fine`,
  `(hover|any-hover): hover`, no `none` bits; `maxTouchPoints` stays 0.
  Constant per persona, no seed; off ⇒ real device answers.
- **0046 SwiftShader sub-pixel precision** — one constant in
  `third_party/swiftshader/src/Vulkan/VkConfig.hpp`
  (`SUBPIXEL_PRECISION_BITS`: 4 → 8, matching hardware rasterizer rules
  and upstream's non-legacy default) so a software-GL rig's coverage
  boundaries snap at 1/256 px like hardware instead of 1/16.
  Unconditional fidelity fix, not fingerprint-gated; the shader-*timing*
  twin of that probe still needs a fast host or a real GPU. Served by
  fetch-upstream's new DEPS-pinned SwiftShader resolution.
