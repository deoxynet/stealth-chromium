# 0041 — Coherent identity across frames under CDP user-agent overrides

Vendor evidence: CloakBrowser Pro CHANGELOG, release 0.5.2 (build
150.0.7871.114.4) — "Consistent identity under CDP user-agent overrides —
subframes no longer disagree with the top frame about the browser identity
when a custom user agent is set over CDP. Mainly affects Puppeteer, which
sets one on every page session."

## Traced override flow at 146.0.7680.177

Every path below was read in the actual tag source (Gitiles), not assumed.

Browser side:

- `content/browser/devtools/protocol/emulation_handler.cc` —
  `EmulationHandler::SetUserAgentOverride(user_agent, accept_language,
  platform, ua_metadata_override)`. Stores per-session state
  (`user_agent_`, `user_agent_metadata_`, `accept_language_`) and returns
  `Response::FallThrough()`, which forwards the command to the frame's
  renderer-side session.
- Stored state is consumed browser-side only:
  `EmulationHandler::ApplyOverrides()` rewrites the `User-Agent` /
  `Accept-Language` request headers, and
  `EmulationHandler::ApplyUserAgentMetadataOverrides()` supplies (or
  withholds) UA-CH metadata. Callers, both keyed by `FrameTreeNode` →
  `RenderFrameDevToolsAgentHost::GetFor()`:
  `content/browser/devtools/devtools_instrumentation.cc`
  (`ApplyNetworkRequestOverrides`, `ApplyUserAgentMetadataOverrides`) and
  `content/browser/client_hints/client_hints.cc`
  (`UpdateNavigationRequestClientUaHeadersImpl`, which notes "if devtools
  says to override client hints but provides no value, disable them").

Renderer side (the JS-visible half):

- `third_party/blink/renderer/core/inspector/inspector_emulation_agent.cc` —
  `InspectorEmulationAgent::setUserAgentOverride()` receives the forwarded
  command for the frame's *local root* (main frame + same-process iframes
  share one `WebDevToolsAgentImpl`; every OOPIF is its own local root and
  gets its own agent, which Puppeteer never sends the command to).
  `ApplyUserAgentOverride()` / `ApplyUserAgentMetadataOverride()` are the
  probe hooks; unspecified metadata fields are filled from
  `Platform::Current()->UserAgentMetadata()` (the persona metadata, pushed
  browser-side via `RenderThreadImpl::user_agent_metadata_`).
- `third_party/blink/renderer/core/loader/frame_loader.cc` —
  `FrameLoader::UserAgent()` = `ApplyUserAgentOverride(Client()->UserAgent())`;
  the probe overwrites the string when the emulation agent holds one.
- `third_party/blink/renderer/core/frame/local_frame_client_impl.cc` —
  `LocalFrameClientImpl::UserAgent()` prefers
  `WebFrameClient::UserAgentOverride()` (the renderer-preferences override
  used by "request desktop site"); `UserAgentMetadata()` prefers
  `WebFrameClient::UserAgentMetadataOverride()` when that override is on,
  then runs `probe::ApplyUserAgentMetadataOverride()` on the result.
- `content/renderer/render_frame_impl.cc` —
  `RenderFrameImpl::UserAgentOverride()` /
  `UserAgentMetadataOverride()` read
  `GetWebView()->GetRendererPreferences().user_agent_override`
  (`blink::UserAgentOverride`, defined in
  `third_party/blink/public/common/user_agent/user_agent_metadata.h`),
  gated by `ShouldUseUserAgentOverride()` — which returns false whenever the
  WebView's main frame is remote (every OOPIF process) and otherwise requires
  the main frame's `DocumentState::is_overriding_user_agent()` from the last
  committed navigation.
- `content/browser/web_contents/web_contents_impl.cc` —
  `WebContentsImpl::SetUserAgentOverride(ua_override, override_in_new_tabs)`
  is the stock, page-wide alternative: sets
  `renderer_preferences_.user_agent_override` and `SyncRendererPrefs()`
  ("Send the new override string to all renderers in the current page" —
  all RenderViewHosts of all FrameTrees, OOPIF processes included).
  `LocalDOMWindow::GetUserAgentMetadata()`
  (`local_frame_client_impl.cc` → `FrameLoader::UserAgentMetadata()`)
  feeds `NavigatorUA::userAgentData()` (`navigator_ua.cc`), which builds the
  JS-visible `NavigatorUAData` from the metadata or from an **empty**
  `UserAgentMetadata` when the probe forced `nullopt`.

## Incoherences found (stock 146, fingerprint on, Puppeteer-style
`Emulation.setUserAgentOverride({userAgent})`)

1. **Top frame vs OOPIFs.** The forwarded command reaches only the main
   frame's local root. Every OOPIF keeps reporting the persona/stock
   `navigator.userAgent` and `navigator.userAgentData` while the top frame
   reports the override — the exact vendor-reported "subframes disagree
   with the top frame" lie (CreepJS "lies" class). `ShouldUseUserAgentOverride()`
   even returns early-false for remote main frames, so the renderer-preferences
   channel cannot pick up the slack in OOPIF processes.
2. **Blank `navigator.userAgentData` next to a spoofed UA string.** With no
   `userAgentMetadata` parameter, the emulation agent's
   `ApplyUserAgentMetadataOverride()` forces `nullopt`;
   `LocalDOMWindow::GetUserAgentMetadata()` turns that into a
   default-constructed (all-empty) `UserAgentMetadata`. Top frame and
   same-process iframes report `brands: [], platform: "", mobile: false`
   beside a non-empty `navigator.userAgent` — no real browser ever does this.
3. **Header vs JS divergence.** `user_agent_metadata_ = nullopt` with
   `user_agent_` set means client hints are *disabled* on main-frame-tree
   requests (client_hints.cc comment quoted above): no `Sec-CH-UA*` headers,
   while JS still reports persona brands in OOPIFs and blanks in the top
   frame. OOPIF navigation requests (no auto-attached DevTools session →
   `GetFor()` returns null) keep persona UA/Sec-CH headers regardless.
4. **Platform fields left at persona values.** Nothing derives
   `platform`/`platformVersion` from the override string, so a
   "Windows NT 10.0" UA override on a macOS persona still carried
   `platform: "macOS"` metadata (and, when a metadata object was passed with
   platform fields omitted, empty strings went straight to JS — stock copies
   `GetPlatform()` etc. verbatim with no persona fallback).

## Patch mechanism

One derivation, applied everywhere, while `fingerprint::Enabled()` is on and
the caller sent a bare UA string (no `acceptLanguage`/`platform`/`userAgentMetadata`
parameters — the Puppeteer case):

- `third_party/blink/public/common/fingerprint/cdp_ua_override.h` (new,
  header-only): `DeriveUserAgentMetadataFromOverride(ua_string, persona)`
  parses the override string — last recognized brand token (`Chrome/`,
  `Chromium/`, `CriOS/`, `Edg/`, `EdgA/`, `EdgiOS/`, `OPR/`, `OPX/`,
  `Vivaldi/`, `SamsungBrowser/`) gives brand + full version; the OS-CPU
  parenthetical (Windows / Android / iOS / CrOS / macOS / Linux) gives
  platform, platform version, architecture, mobile, model. Brand lists are
  rebuilt with the stock GREASE algorithm
  (`GetGreasedUserAgentBrandVersion` + `ShuffleBrandList`, reimplemented;
  seeded by the *override's* major version) in the same three-entry shape
  0010 uses. The persona (0010's `GetUserAgentMetadata()`) fills everything
  the string cannot imply, and its tuned platform fields are kept whenever
  the string's OS matches the persona platform. Pure and deterministic:
  same override string + same seed ⇒ identical metadata every launch.
- `emulation_handler.cc` `SetUserAgentOverride()`: derives the metadata,
  stores it in `user_agent_metadata_` (so browser-side Sec-CH generation and
  header overrides follow the same derivation), routes the whole override
  through `WebContentsImpl::SetUserAgentOverride({string, derived metadata})`
  (renderer preferences reach every frame's process, OOPIFs included), and
  returns `Response::Success()` — handling the command browser-side so the
  renderer-side emulation agent never sees it and cannot blank
  `navigator.userAgentData`. When the client *does* pass a
  `userAgentMetadata` object, stock field-by-field conversion is kept but
  unspecified fields (brand lists, full version, bitness, wow64, form
  factors, and empty platform strings) fall back to the derivation instead
  of the raw persona, and the result is likewise propagated to every frame.
  `Disable()` and an explicit empty-string override clear the
  WebContents-level override again.
- `render_frame_impl.cc` `ShouldUseUserAgentOverride()`: while fingerprinting
  is on, a renderer-preferences override is honored in *every* frame,
  removing the remote-main-frame and per-navigation-flag gates that kept
  OOPIFs (and not-yet-navigated frames) on the persona identity.

Resulting getter ladder everywhere: **CDP override (derived, one derivation)
> persona (0010) > stock** — identical in the top frame, same-process
iframes, and OOPIFs, for `navigator.userAgent`, `navigator.appVersion`
(derived from the UA string), and all `navigator.userAgentData` fields.

## Off-semantics divergence from the vendor

The vendor build applies its fix unconditionally. We gate the entire
mechanism on `fingerprint::Enabled()`: with fingerprinting off the handler
keeps stock behavior bit-for-bit (stored string, nullopt metadata,
fall-through to the renderer agent), and `ShouldUseUserAgentOverride()`
regains its stock gates. Known residuals under the patch (documented, both
outside the file lanes of this patch):

- `navigator.platform` (the JS string "Win32"/"MacIntel", from
  `NavigatorBase::platform()` in blink, not client-hints metadata) keeps the
  0011 persona value when the override implies another OS — this matches
  stock Chrome, whose `--user-agent` also does not move `navigator.platform`.
  `userAgentData.platform` and the `Sec-CH-UA-Platform` headers *are*
  derived from the override.
- OOPIF navigation requests whose FrameTreeNode has no auto-attached DevTools
  session still compute their header UA in `NavigationRequest` from the
  entry-level override flag (`is_overriding_user_agent()`); subresource
  fetches and all JS-visible surfaces inside the frame do report the
  override.
- Callers that pass explicit `platform`/`acceptLanguage` parameters keep the
  stock renderer-side path (explicit control beats coherence), so those
  sessions retain the stock per-local-root behavior.

## Test plan (real build)

1. Launch with `--fingerprint=1` (persona macOS) and drive with Puppeteer:
   `page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/120.0.6099.109 Safari/537.36')`,
   then load a page with (a) a same-origin iframe, (b) a cross-origin iframe
   (OOPIF). In each frame evaluate and compare:
   `navigator.userAgent`,
   `navigator.userAgentData.brands`,
   `navigator.userAgentData.platform`,
   `await navigator.userAgentData.getHighEntropyValues(['platformVersion','fullVersionList','architecture','model'])`.
   Expect: identical across all three frames; brands contain
   `Google Chrome;120`, GREASE `Not_A Brand;8` (seed 120);
   `platform === 'Windows'`, `platformVersion === '19.0.0'`.
2. Header side: serve an endpoint echoing request headers; fetch it from the
   top frame and from inside the OOPIF; compare `User-Agent` and `Sec-CH-UA`
   against the JS-visible values (they must match the same derivation).
3. Persona-fill: repeat with a UA string whose OS equals the persona OS
   (macOS) — platform detail fields must stay at the persona values
   (`15.2.0`, `arm`), only the version moves.
4. Determinism: relaunch with the same `--fingerprint` seed and identical
   override; dump `getHighEntropyValues` JSON — must be byte-identical.
5. Off-semantics: repeat step 1 with `--fingerprint=off`; stock behavior
   must be preserved (top frame blank `userAgentData`, OOPIFs persona,
   `Emulation.setUserAgentOverride({userAgent, platform})` still applies the
   platform). Also verify `Emulation.disable` / re-`setUserAgentOverride('')`
   restores the persona identity in every frame.

Sketch:

```js
const browser = await puppeteer.launch({executablePath: './chrome',
  args: ['--fingerprint=1', '--no-sandbox']});
const page = await browser.newPage();
await page.setUserAgent(
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) ' +
  'Chrome/120.0.6099.109 Safari/537.36');
await page.goto('https://test.example/frame-matrix');
const collect = () => page.evaluate(async () => {
  const frames = [document.documentElement.outerHTML && navigator.userAgent];
  return {
    ua: navigator.userAgent,
    uad: await navigator.userAgentData.getHighEntropyValues(
      ['platformVersion', 'fullVersionList', 'architecture', 'model']),
    iframes: await Promise.all(
      [...document.querySelectorAll('iframe')].map(async f => {
        const w = f.contentWindow;
        return {ua: w.navigator.userAgent,
                brands: JSON.stringify(w.navigator.userAgentData.brands),
                platform: w.navigator.userAgentData.platform};
      }))};
});
const top = await collect();
// assert top.iframes.every(f => f.ua === top.ua) and platform === 'Windows'.
```
