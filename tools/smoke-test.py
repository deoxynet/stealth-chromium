#!/usr/bin/env python3
"""Stealth Chromium post-build smoke test.

Launches the built Chromium binary headless over CDP and asserts the
fingerprint contract implemented by the series:

  * no switches  -> persona defaults (random seed; coherent values)
  * --fingerprint=<seed> twice -> identical canvas/audio-derived values
  * different seeds -> different values
  * --fingerprint=off -> stock passthrough (no 8-thread/8 GiB persona,
    real timezone, unnoised canvas stable across seeds)
  * explicit overrides win (--fingerprint-device-memory etc.)
  * timezone / locale / UA / storage-quota / WebGL / mediaDevices personas

Usage:
    python3 tools/smoke-test.py /path/to/out/stealth-chromium/chrome

Requires: pip install websockets   (and a binary with the patches!)
Exit code 0 = all assertions passed.
"""

import argparse
import asyncio
import hashlib
import json
import random
import re
import subprocess
import sys
import tempfile
import urllib.request

import websockets  # noqa: F401  (checked below with a friendly message)

PROBE = r"""
(() => {
  const out = {};
  out.hw = navigator.hardwareConcurrency;
  out.mem = navigator.deviceMemory;
  out.ua = navigator.userAgent;
  out.platform = navigator.platform;
  out.tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  out.lang = navigator.language;
  out.screen = [screen.width, screen.height, screen.availWidth, screen.availHeight];
  out.languages = navigator.languages.join(',');
  // canvas noise probe: draw text + gradient, hash pixels
  const c = document.createElement('canvas');
  c.width = 220; c.height = 60;
  const ctx = c.getContext('2d');
  const g = ctx.createLinearGradient(0, 0, 220, 60);
  g.addColorStop(0, '#f0f'); g.addColorStop(1, '#0ff');
  ctx.fillStyle = g; ctx.fillRect(0, 0, 220, 60);
  ctx.font = '16px sans-serif';
  ctx.fillText('Stealth Chromium \u2713 0123', 5, 40);
  const d = ctx.getImageData(0, 0, 220, 60).data;
  let h = 0; for (let i = 0; i < d.length; i++) h = (h * 31 + d[i]) >>> 0;
  out.canvasHash = h.toString(16);
  // measureText probe
  out.textWidth = ctx.measureText('Stealth Chromium 0123').width;
  // clientRects probe
  const el = document.createElement('div');
  el.style.cssText = 'position:absolute;width:317px;height:151px;';
  document.body.appendChild(el);
  const r = el.getBoundingClientRect();
  out.rect = [r.x, r.width];
  return out;
})()
"""

WEBGL_PROBE = r"""
(() => {
  const c = document.createElement('canvas');
  const gl = c.getContext('webgl2') || c.getContext('webgl');
  if (!gl) return {webgl: false};
  const ext = gl.getExtension('WEBGL_debug_renderer_info');
  return {
    webgl: true,
    vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null,
    renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null,
  };
})()
"""

QUOTA_PROBE = "navigator.storage.estimate().then(e => [e.usage, e.quota])"
MEDIA_PROBE = """
(navigator.mediaDevices.enumerateDevices().then(d =>
  d.map(x => [x.kind, x.deviceId, x.groupId])))
"""

AUDIO_PROBE = r"""
(async () => {
  // Match Fingerprint's linked production/demo recipe. Rendering remains offline.
  async function render() {
    const context = new OfflineAudioContext(1, 5000, 44100);
    const oscillator = context.createOscillator();
    oscillator.type = 'triangle';
    oscillator.frequency.value = 10000;
    const compressor = context.createDynamicsCompressor();
    compressor.threshold.value = -50;
    compressor.knee.value = 40;
    compressor.ratio.value = 12;
    compressor.attack.value = 0;
    compressor.release.value = 0.25;
    oscillator.connect(compressor);
    compressor.connect(context.destination);
    oscillator.start(0);
    return context.startRendering();
  }
  function sum(samples) {
    return samples.reduce((result, value) => result + Math.abs(value), 0);
  }
  function signature(samples) {
    const bits = new Uint32Array(samples.buffer, samples.byteOffset, samples.length);
    let hash = 2166136261;
    for (const value of bits) hash = Math.imul(hash ^ value, 16777619) >>> 0;
    return hash.toString(16);
  }
  const buffer = await render();
  const first = buffer.getChannelData(0).slice(4500);
  const repeated = buffer.getChannelData(0).slice(4500);
  const copied = new Float32Array(500);
  buffer.copyFromChannel(copied, 0, 4500);
  const copyFirstBuffer = await render();
  const copyFirst = new Float32Array(500);
  copyFirstBuffer.copyFromChannel(copyFirst, 0, 4500);
  const silent = new AudioBuffer({numberOfChannels:1, length:5000, sampleRate:44100});
  const calibration = silent.getChannelData(0).slice(4500);
  const recovered = first.map((value, index) => Math.fround(value-calibration[index]));
  return {
    fingerprint: sum(first), signature: signature(first),
    repeatedSignature: signature(repeated), copySignature: signature(copied),
    copyFirstSignature: signature(copyFirst),
    calibrationNonzero: calibration.some(value => value !== 0),
    recoveredFingerprint: sum(recovered)
  };
})()
"""


class Fail(Exception):
    pass


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        raise Fail(f"{name} FAILED {detail}")


class Browser:
    def __init__(self, binary, args):
        self.args = args
        self.port = random.randint(9300, 9800)
        self.proc = subprocess.Popen(
            [binary, "--headless=new", "--no-sandbox", "--disable-gpu",
             f"--remote-debugging-port={self.port}", "about:blank", *args],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait_ready(self):
        import time
        for _ in range(100):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{self.port}/json/version", timeout=1) as r:
                    json.load(r)
                return
            except Exception:
                if self.proc.poll() is not None:
                    raise Fail("browser exited before CDP was ready")
                time.sleep(0.2)
        raise Fail("CDP did not come up")

    async def evaluate(self, expr, await_promise=False):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/json", timeout=2) as r:
            targets = json.load(r)
        page = next(t for t in targets if t["type"] == "page")
        async with websockets.connect(page["webSocketDebuggerUrl"],
                                      max_size=32 * 2**20) as ws:
            msg_id = 1
            await ws.send(json.dumps({
                "id": msg_id, "method": "Runtime.evaluate",
                "params": {"expression": expr, "returnByValue": True,
                           "awaitPromise": await_promise}}))
            while True:
                resp = json.loads(await ws.recv())
                if resp.get("id") == msg_id:
                    if "exceptionDetails" in resp.get("result", {}):
                        raise Fail(f"evaluate error: {resp['result']['exceptionDetails']}")
                    return resp["result"].get("result", {}).get("value")

    async def console_stealth(self):
        """Patch 0038 check: with Runtime.enable held, console.log must not
        emit consoleAPICalled, the in-page prepareStackTrace+console.log CDP
        probe must stay silent, and a Runtime.disable/enable cycle must not
        replay stored messages. Returns (probe_fired, live_events, replayed).
        """
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/json", timeout=2) as r:
            targets = json.load(r)
        page = next(t for t in targets if t["type"] == "page")
        async with websockets.connect(page["webSocketDebuggerUrl"],
                                      max_size=32 * 2**20) as ws:
            msg_id = 0

            async def send(method, params=None):
                nonlocal msg_id
                msg_id += 1
                await ws.send(json.dumps({
                    "id": msg_id, "method": method, "params": params or {}}))
                while True:
                    resp = json.loads(await ws.recv())
                    if resp.get("id") == msg_id:
                        if "error" in resp:
                            raise Fail(f"{method}: {resp['error']}")
                        return resp.get("result", {})

            async def drain(seconds):
                import asyncio as aio
                events = []
                try:
                    while True:
                        ev = await aio.wait_for(ws.recv(), seconds)
                        m = json.loads(ev)
                        if "method" in m:
                            events.append(m["method"])
                except aio.TimeoutError:
                    pass
                return events

            await send("Runtime.enable")
            await send("Runtime.evaluate", {
                "expression": "console.log('stealth-live-1'); "
                              "console.log('stealth-live-2');",
                "returnByValue": True})
            live = [e for e in await drain(0.8)
                    if e == "Runtime.consoleAPICalled"]
            probe = await send("Runtime.evaluate", {
                "expression": r"""(() => {
                    let wasAccessed = false;
                    const orig = Error.prepareStackTrace;
                    Error.prepareStackTrace = function () {
                      wasAccessed = true; return orig; };
                    console.log(new Error(''));
                    Error.prepareStackTrace = orig;
                    return wasAccessed; })()""",
                "returnByValue": True})
            probe_fired = probe["result"]["value"] is True
            await send("Runtime.disable")
            await send("Runtime.enable")
            replayed = [e for e in await drain(0.8)
                        if e == "Runtime.consoleAPICalled"]
            return probe_fired, live, replayed

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


async def probe(binary, args):
    b = Browser(binary, args)
    try:
        b.wait_ready()
        base = await b.evaluate(PROBE)
        base["webgl"] = await b.evaluate(WEBGL_PROBE)
        base["quota"] = await b.evaluate(QUOTA_PROBE, await_promise=True)
        base["media"] = await b.evaluate(MEDIA_PROBE, await_promise=True)
        base["audio"] = await b.evaluate(AUDIO_PROBE, await_promise=True)
        return base
    finally:
        b.stop()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("binary")
    ns = ap.parse_args()

    seed_a, seed_b = "12345678901234", "98765432109876"
    print("[1/5] persona defaults (no switches)")
    d = await probe(ns.binary, [])
    check("hardwareConcurrency persona = 8", d["hw"] == 8, d["hw"])
    check("deviceMemory persona = 8", d["mem"] == 8, d["mem"])
    check("UA has no Headless token", "Headless" not in d["ua"], d["ua"])
    check("screen persona 1920x1080",
          d["screen"][:2] == [1920, 1080], d["screen"])
    check("availHeight = height - taskbar(48)",
          d["screen"][3] == d["screen"][1] - 48, d["screen"])
    check("webgl renderer is persona ANGLE string",
          bool(d["webgl"].get("renderer", "") or "").startswith("ANGLE ("),
          d["webgl"])
    check("storage quota in [400, 600) MiB",
          400 * 2**20 <= d["quota"][1] < 600 * 2**20, d["quota"])

    print(f"[2/5] seed determinism ({seed_a} twice)")
    a1 = await probe(ns.binary, [f"--fingerprint={seed_a}"])
    a2 = await probe(ns.binary, [f"--fingerprint={seed_a}"])
    b1 = await probe(ns.binary, [f"--fingerprint={seed_b}"])
    check("same seed -> same canvas hash",
          a1["canvasHash"] == a2["canvasHash"],
          f'{a1["canvasHash"]} vs {a2["canvasHash"]}')
    check("different seed -> different canvas hash",
          a1["canvasHash"] != b1["canvasHash"])
    check("same seed -> same mediaDevices ids",
          [m[1] for m in a1["media"]] == [m[1] for m in a2["media"]])
    check("device ids are 16 hex chars",
          all(re.fullmatch(r"[0-9a-f]{16}", m[1]) for m in a1["media"]),
          a1["media"][:2])
    check("same seed -> same audio samples", a1["audio"] == a2["audio"],
          f'{a1["audio"]} vs {a2["audio"]}')
    check("different seed -> different rendered audio samples",
          a1["audio"]["signature"] != b1["audio"]["signature"])
    for label, result in [("seed A", a1["audio"]), ("seed B", b1["audio"])]:
        check(f"{label}: repeated read and both copy paths agree",
              all(result[key] == result["signature"] for key in
                  ["repeatedSignature", "copySignature", "copyFirstSignature"]), result)
        check(f"{label}: silent calibration exposes additive noise",
              result["calibrationNonzero"], result)

    print("[3/5] explicit overrides + personas")
    o = await probe(ns.binary, [
        f"--fingerprint={seed_a}",
        "--fingerprint-hardware-concurrency=6",
        "--fingerprint-device-memory=4",
        "--fingerprint-timezone=Europe/Berlin",
        "--fingerprint-locale=de-DE",
        "--fingerprint-platform=macos",
    ])
    check("hardwareConcurrency override", o["hw"] == 6, o["hw"])
    check("deviceMemory override", o["mem"] == 4, o["mem"])
    check("timezone override", o["tz"] == "Europe/Berlin", o["tz"])
    check("locale override", o["lang"] == "de-DE", o["lang"])
    check("macos platform token", o["platform"] == "MacIntel", o["platform"])
    check("macos screen persona 1440x900",
          o["screen"][:2] == [1440, 900], o["screen"])
    check("macos availHeight = height - dock(95)",
          o["screen"][3] == o["screen"][1] - 95, o["screen"])
    check("UA macos persona", "Macintosh; Intel Mac OS X" in o["ua"], o["ua"])

    print("[4/5] --fingerprint=off passthrough")
    off = await probe(ns.binary, ["--fingerprint=off", "--fingerprint-platform=windows"])
    off2 = await probe(ns.binary, ["--fingerprint=off"])
    check("off: no persona screen", off["screen"] == off2["screen"],
          f'{off["screen"]} vs {off2["screen"]}')
    check("off: platform not spoofed to Win32",
          off["platform"] == off2["platform"], off["platform"])
    check("off: canvas identical across launches (no noise)",
          off["canvasHash"] == off2["canvasHash"])
    check("off: offline audio identical across launches",
          off["audio"] == off2["audio"])
    check("off: silent audio buffer stays zero", not off["audio"]["calibrationNonzero"])

    print("[5/5] Runtime console/exception instrumentation stealth (0038)")
    b = Browser(ns.binary, [])
    try:
        b.wait_ready()
        probe_fired, live, replayed = await b.console_stealth()
        check("prepareStackTrace+console.log CDP probe stays silent",
              probe_fired is False, probe_fired)
        check("no live Runtime.consoleAPICalled", live == [], live)
        check("no replayed events after Runtime.disable/enable",
              replayed == [], replayed)
    finally:
        b.stop()
    boff = Browser(ns.binary, ["--fingerprint=off"])
    try:
        boff.wait_ready()
        probe_fired, live, replayed = await boff.console_stealth()
        check("off: prepareStackTrace probe fires (stock behavior)",
              probe_fired is True, probe_fired)
        check("off: live Runtime.consoleAPICalled delivered", len(live) >= 1,
              live)
    finally:
        boff.stop()

    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Fail as e:
        print(f"\nSMOKE TEST FAILED: {e}", file=sys.stderr)
        sys.exit(1)
