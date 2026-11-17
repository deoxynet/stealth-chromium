# Reported Chrome version override (patch 0040)

The reported Chrome version is decoupled from the Chromium base the browser
is built from: web-reachable surfaces, `--version`, and `chrome://version`
all report a current public Chrome version instead of the build base's
`PRODUCT_VERSION`.

## Motivation

A stealth browser is frozen on whatever Chromium base it was last rebased
onto (this series: 146.0.7680.177). A browser that reports a year-stale
version while claiming to be regular Chrome is one of the cheapest bot
signals there is: no script sophistication needed, just
`navigator.userAgent` against a table of live Chrome versions. The vendor
build shipped exactly this fix:

> "**The reported Chrome version tracks real Chrome**, independent of the
> version we build from."
> — CloakBrowser Pro CHANGELOG, 0.4.12 (Chromium 150.0.7871.114.3)

> "**One consistent reported build number** — `--version` and
> `chrome://version` now read the public version from a single source.
> Web-reachable surfaces, including the user agent, are unchanged."
> — CloakBrowser Pro CHANGELOG, 0.5.2 (Chromium 150.0.7871.114.4)

Interpretation: the vendor introduced a single source of truth for the
reported version. The 0.5.2 build-number unification covered
`--version`/`chrome://version`; UA surfaces already tracked the public
version since 0.4.12, so they were "unchanged" *by that release*. This
patch unifies both on one helper from the start.

## Design

**Ladder (single source, `base/version_info/version_info.cc`):**

1. `--fingerprint-report-version=<major.minor.build.patch>` — explicit
   override, loosely validated (any dotted-numeric form `base::Version`
   accepts; garbage falls through to the next rung);
2. compile-time default `kDefaultReportedChromeVersion` = Chrome stable at
   the last series rebase (currently `157.0.8103.78`, Chrome stable of
   2026-11-11);
3. fingerprinting off ⇒ stock build version (`PRODUCT_VERSION`),
   byte-for-byte.

**Why `base/`?** Grounding at tag 146.0.7680.177: `components/version_info`
is a facade; since the module moved, `GetVersionNumber()` is a `constexpr`
in `base/version_info/version_info.h` returning the `PRODUCT_VERSION`
macro, and there is no runtime `version_info.cc` left to hook. Both
`--version` (`chrome/app/chrome_main_delegate.cc`,
`HandleVersionSwitches`) and `chrome://version`
(`chrome/browser/ui/webui/version/version_ui.cc`) call
`version_info::GetVersionNumber()` — so de-constexpr-izing that one
function and putting the ladder behind it covers both surfaces with **no
UI or main-delegate edits**. `base/` may not depend on `//third_party/blink`,
so the switch names are spelled out as literals there (mirroring
`blink::switches::kFingerprint`/`kFingerprintReportVersion`), and
`base::CommandLine` is already an allowed dependency.

**Blink-side helper** (`third_party/blink/public/common/fingerprint/
reported_version.h`): `blink::fingerprint::ReportedChromeVersion()`
returns the single source's value when fingerprinting is enabled and `""`
when it is off, so call sites gate on it and degrade to stock values. It
delegates rather than re-implementing the ladder — one definition of the
override precedence and of the rebase-time constant.

## Consumers

| Surface | Site at 146.0.7680.177 | Route |
|---|---|---|
| `--version` / `--product-version` output | `chrome/app/chrome_main_delegate.cc` `HandleVersionSwitches` (~line 366) | `version_info::GetVersionNumber()` |
| `chrome://version` | `chrome/browser/ui/webui/version/version_ui.cc` (~lines 236, 323) | `version_info::GetVersionNumber()` |
| Classic UA `Chrome/<v>` token, full form | `components/version_info/version_info_with_user_agent.cc` | `"Chrome/" + GetVersionNumber()` |
| Classic UA token, reduced form (`<major>.0.0.0`) | same file, `GetProductNameAndVersionForReducedUserAgent()` | via `GetMajorVersionNumber()` → `GetVersion()` → `GetVersionNumber()` |
| UA-CH `Sec-CH-UA` / `Sec-CH-UA-Full-Version-List` brands, `Sec-CH-UA-Full-Version` | `components/embedder_support/user_agent_utils.cc` (stock builders ~lines 184/206, `metadata.full_version` ~line 766) | `version_info::GetVersionNumber()` |
| UA persona brand versions + GREASE seed | `user_agent_utils.cc` persona blocks (~lines 317 and 780) | `blink::fingerprint::ReportedChromeVersion()` |

Everything flows from `GetVersionNumber()`, so the classic UA, UA-CH brands,
`fullVersionList`, the GREASE permutation seed (major), `--version`, and
`chrome://version` cannot disagree with each other.

## Off-semantics

`--fingerprint=off` (also `false`/`0`/`disable`/`disabled`): the ladder in
`base/version_info/version_info.cc` returns `PRODUCT_VERSION`, the UA
facade returns `"Chrome/" PRODUCT_VERSION`, and
`ReportedChromeVersion()` returns `""` so the persona call sites in
`user_agent_utils.cc` fall back to the stock value — every touched surface
is byte-for-byte stock Chromium. The value is resolved once per process and
cached (same convention as the core `fingerprint::Enabled()`), so it is
stable for the lifetime of the process. Renderer processes see the same
value because patch 0003's propagation table (extended here with
`kFingerprintReportVersion`) forwards the switches; GPU/utility processes
do not get them and keep reporting the real build version, which is
desirable for crash triage.

## Known risks

- **Version-vs-feature-detection mismatch**: the browser *reports* 157
  while *implementing* 146. A script that feature-detects (or
  bug-detects) post-146 behavior and compares against the claimed version
  sees an impossible combination. This is inherent to any frozen-base
  stealth browser and grows with the base's age; the mitigation is
  rebasing, not detection.
- **Update/telemetry services** that embed `GetVersionNumber()` (component
  updater proxies, variations headers) will request metadata for the
  public version, not the build. Harmless for a manually-managed stealth
  build, but worth knowing when debugging update behavior.
- **Non-Chrome brand personas** (Edge/Opera/Vivaldi from patch 0010) keep
  their own static default versions; only the Chrome token in their UA
  tracks the reported version. A real Edge release always matches Chrome's
  major, so long-lived sessions may want an explicit
  `--fingerprint-brand-version` alongside the platform switches.
- `GetVersionNumber()` and `GetProductNameAndVersionForUserAgent()` are
  upstream `constexpr`; both become runtime functions here. Call sites
  that use them in constant expressions (none known at 146; all uses found
  are runtime) would break — re-check on rebase if compilation fails.

## Maintenance

At each rebase, update `kDefaultReportedChromeVersion` in
`base/version_info/version_info.cc` to the then-current Chrome stable
version (see docs/REBASING.md, "Reported version constant"). One constant,
every surface follows.
