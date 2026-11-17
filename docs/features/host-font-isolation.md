# Host-font isolation (0042)

Closes the series' documented persona gap: under an active fingerprint
persona, the *reported* font platform and the persona font lists were
already spoofed (0022), but the host's actually-installed fonts stayed
observable through font availability. A page could ask for a family that
exists on the host but not on the persona OS and watch it resolve —
`document.fonts.check`, text-measurement probing, and `@font-face
src: local()` all answered from the host install set. 0042 makes font
availability follow the persona inventory alone.

## Motivation

Vendor evidence (CloakBrowser Pro `CHANGELOG.md`):

- 0.5.8 (2026-08-18): "improved host-font isolation for cross-platform
  personas."
- 0.7.2 (2026-11-10): "the Windows persona's system and code fonts now
  match real Windows by default."

Workspace evidence (`patches/EMPIRICAL.md`, new observation 2):

> `document.fonts.check('72px "Ubuntu"')` still returns **true under the
> Windows persona** — the font DB patch (18) acts at FontCache matching
> level; `fonts.check` consults a path that reports local availability.
> Potential persona leak (class: plugins/codec gaps). MEASUREMENTS still
> shift per persona (metric deltas observed).

0022's gate was fail-open: a family was blocked only when it appeared in
the *recovered host inventory* but not in the persona list. The recovered
inventories are samples (e.g. 47 names for Linux), so any host font
outside the sample — every user-installed font — resolved through the
stock fontconfig path, and only cross-platform personas were gated.

How the leak reads out at 146 (verified against stock Chrome):

- `document.fonts.check('12px <family>')` with no `@font-face` for
  `<family>` returns true regardless of installation — but with a
  same-family webfont shim (`@font-face { font-family: X; src: url(...) }`
  that never loads), `check('12px X')` returns **true when X is installed
  locally** (platform availability short-circuits the load check) and
  **false when it is not**. That is a per-family availability oracle.
- `measureText`/layout: a family that resolves to a real host font
  measures differently from one that falls back — the "MEASUREMENTS still
  shift" observation.
- `@font-face { src: local("X") }` + `document.fonts.load('12px X')`:
  the promise settles `loaded` vs `error` exactly by local availability
  (`LocalFontFaceSource::IsValid`).

## Mechanism

### Choke point

`FontCache::GetFontPlatformData` (`third_party/blink/renderer/platform/
fonts/font_cache.cc`) — the single funnel every family-keyed platform
font lookup passes through at 146, regardless of API:

| Surface | Path to the choke point |
|---|---|
| CSS `font-family` matching / layout / canvas `measureText` | `CSSFontSelector::GetFontData` → `FontCache::GetFontData` → `GetFontPlatformData` |
| `document.fonts.check` | `FontFaceSet::check` → `CSSFontSelectorBase::IsPlatformFamilyMatchAvailable` → `FontCache::IsPlatformFamilyMatchAvailable` → `GetFontPlatformData` |
| `@font-face src: local()` / `document.fonts.load` | `LocalFontFaceSource::IsValid`/`CreateFontData` → `FontCache::IsPlatformFontUniqueNameMatchAvailable` → `GetFontPlatformData` |
| `system-ui` | `FontCache::SystemFontPlatformData` recurses into `GetFontPlatformData` with the host system family |

Lower alternatives exist (`CreateFontPlatformData`/`CreateTypeface` in
`platform/fonts/skia/font_cache_skia.cc`) but they are per-port (the mac
port compiles `font_cache_coretext.cc` instead) and sit below the
platform-data cache, so a gate there needs one copy per port and only
runs on cache misses. Higher alternatives (`CSSFontSelector`,
`FontFaceSet::check`) would need one patch per surface and would miss
the plain CSS path. One choke point beats per-API patches. 0022 already
routed `GetFontPlatformData` through `fingerprint_fonts::ResolveSpoofedFamily`;
0042 swaps that call for `fingerprint_fonts::ResolveIsolatedFamily`
(`fingerprint_host_font_isolation.h`) at the same site. The decision
lives in a new header that reuses 0022's inventories, basic set,
fonts-dir scan, and alias map verbatim — `fingerprint_font_databases.h`
stays byte-identical, matching the series' one-surface-one-header idiom
(cf. 0026, 0032, 0034), and `ResolveSpoofedFamily` remains in it for
spec provenance.

### Allowlist

When `fingerprint::Enabled()` and a persona platform is active
(`SpoofedPlatform() != kNone`), a family-keyed lookup resolves only if:

1. it is in the persona's recovered inventory
   (`fingerprint_fonts::GetPlatformFonts(spoofed)`), or
2. `--fingerprint-fonts-dir` ships a file whose basename matches it
   (`FontsDirProvides`, scanned once), or
3. it is a generic/basic family (`IsBasicFont`: `serif`, `sans-serif`,
   `monospace`, `cursive`, `fantasy`, `system-ui`, `-webkit-standard`,
   `math`, plus the always-safe set `arial`, `courier new`, `roboto`,
   `sans`, `times new roman`, `blinkmacsystemfont`, `noto color emoji`,
   `dejavu math tex gyre`, `franklin gothic[- medium]`,
   `microsoft sans serif`).

Everything else returns `kUnavailable` → `nullptr`, which is bit-for-bit
what stock Chromium returns for a family that is not installed. Unknown
families fail closed; the host inventory is not consulted at all, so
same-platform personas (e.g. `--fingerprint-platform=linux` on a Linux
host) clamp to the persona list exactly like cross-platform ones.
Families in the Windows persona list are still re-aimed at
metric-compatible local fonts by 0022's alias map (unchanged). In-buffer
webfonts (`kCreateFontByFciIdAndTtcIndex`) never take the family path and
are untouched — remote `@font-face` fonts keep working whatever their
family name.

Determinism: availability follows the persona, not the seed. No per-seed
derivation anywhere in this patch.

### Cost

The gate runs on every `GetFontPlatformData` call: two cached static
switch reads, one lowercase/UTF-8 copy (already paid by 0022), and
O(log n) lookups in `base::NoDestructor` sets built once (≤ 554 entries,
~10 string compares). This is noise next to the fontconfig/Skia match it
guards, and it is *cheaper* than 0022's rule, which additionally probed
the host inventory on every miss.

## Surfaces covered

- **CSS font matching**: non-allowlisted families fall through to the
  next family in the stack, then the stock fallback chain — the standard
  missing-font path.
- **`document.fonts.check`**: the availability bit it consults
  (`IsPlatformFamilyMatchAvailable`) now reports the persona, so the
  shim-based oracle above answers for "Ubuntu" exactly as a real Windows
  box would (false, with the shim). Without a shim, stock semantics
  return true for every family — on the persona too — which also matches
  stock-on-the-persona-OS.
- **`measureText`/layout/canvas**: non-allowlisted families measure and
  render with the fallback, so text-width probing cannot distinguish
  "host has this font" from "nobody has this font".
- **`src: local(...)`/`fonts.load`**: unique-name (PostScript/full name)
  lookups go through the same family gate, so `local()` sources for
  non-allowlisted names fail and the face falls to its next source. This
  also means PostScript-name forms of *persona* families (e.g.
  `local("ArialMT")`) fall back to the next `src` entry, since the
  inventories carry family names — strict, and deliberate: the unique
  name path is the enumeration oracle.
- **`window.queryLocalFonts`** (Local Font Access): shipped-stable at
  146 (`runtime_enabled_features.json5`, desktop) but requires a secure
  context, user activation, and an explicit permission grant, so it is
  not a passive fingerprinting vector. It enumerates the host list
  directly and is **not** persona-filtered by this patch — out of the
  file lane; follow-up patch would filter `FontMetadata` results through
  the same allowlist.
- **Per-character glyph fallback**
  (`FontCache::FallbackFontForCharacter` → fontconfig) is deliberately
  not gated: it serves script coverage, not family naming, and blocking
  it would produce tofu. Its metric fingerprint is handled by 0031.

## Rendering consequence (intended)

Pages that ask for host-only exotic fonts now render them with the
fallback chain. That degradation *is* host-font isolation — the vendor's
documented semantics — and it matches what the same page would look like
on a real machine running the persona OS. Generic families and the
last-resort spine (`sans`, `arial`, `legacyMakeTypeface`) are all in the
always-allow set, so no page can end up without renderable text.

## Off semantics

`--fingerprint=off|false|0|disable|disabled` (or no persona switch)
⇒ `Enabled()`/`SpoofedPlatform()` gate returns `kStock` before any
string work ⇒ byte-for-byte stock font behavior, including fontconfig's
usual substitution. No new switch is introduced; persona configuration
is `--fingerprint` + `--fingerprint-platform` + optional
`--fingerprint-fonts-dir`, all pre-existing from 0001/0022.

## Test plan sketch

- Linux host, `--fingerprint=1 --fingerprint-platform=windows`:
  - `check('12px Segoe UI')` (persona list) → true; shim-oracle
    `check('12px Ubuntu')` with a never-loading same-family `@font-face`
    → false (matches real Windows).
  - `measureText` width of `12px Fira Code` (host-installed, not in the
    Windows list) equals the fallback width, and differs from a vanilla
    run on the same host.
  - `new FontFace('P', 'local("Ubuntu")').load()` rejects.
- Same host, `--fingerprint-platform=linux`: host-installed fonts
  outside the 47-name Linux inventory stop resolving (clamped to the
  persona list).
- `--fingerprint=off`: every probe byte-identical to vanilla stock.
- `--fingerprint-fonts-dir` with `Segoe UI.ttf`: `check('12px Segoe UI')`
  stays true and the family resolves for measurement.
- Determinism: two seeds, same persona ⇒ identical availability answers.
