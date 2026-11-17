#!/usr/bin/env python3
"""Compile extracted patch code with small Blink adapters, then exercise readback.

Run tools/verify.sh first to populate the pristine pinned source mirror.
This is an isolated C++ regression harness, not a Blink/Chromium build. The
noise/hash functions and AudioBuffer method bodies are copied verbatim from
the applied sources; only containers, exceptions and command-line access are
adapted. In particular, no namespace alias repairs the code under test.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / ".upstream-mirror/146_0_7680_177"
WEB_AUDIO = Path("third_party/blink/renderer/modules/webaudio")


def function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    cursor = opening + 1
    while depth:
        depth += (source[cursor] == "{") - (source[cursor] == "}")
        cursor += 1
    return source[start:cursor]


ADAPTERS = r"""
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <iostream>
#include <memory>
#include <string>
#include <string_view>
#include <vector>
#define DCHECK(x) assert(x)
#define UNSAFE_TODO(x) x
namespace base {
template<class T> struct span {
  T* p; size_t n;
  T* data() const { return p; }
  size_t size() const { return n; }
  span first(size_t count) const { return {p, count}; }
  span subspan(size_t offset, size_t count) const { return {p+offset,count}; }
  template<class U> span(const span<U>& other):p(other.p),n(other.n) {}
  span(T* p, size_t n):p(p),n(n) {}
  void copy_from(span<const float> other) { std::copy_n(other.p, n, p); }
};
}
namespace blink::fingerprint {
bool noise_enabled = true;
bool NoiseEnabled() { return noise_enabled; }
std::string SeedString() { return "1"; }
"""

BLINK = r"""
namespace blink {
template<class T> struct NotShared {
  T* p;
  explicit NotShared(T* value):p(value) {}
  T* operator->() const { return p; }
  explicit operator bool() const { return p != nullptr; }
};
struct DOMFloat32Array {
  std::vector<float> samples;
  explicit DOMFloat32Array(size_t size):samples(size) {}
  size_t length() const { return samples.size(); }
  float* Data() { return samples.data(); }
  base::span<float> AsSpan() { return {Data(),length()}; }
};
template<class T> struct Member {
  std::shared_ptr<T> p;
  T* Get() { return p.get(); }
};
enum class DOMExceptionCode { kIndexSizeError };
struct ExceptionState {
  bool raised = false;
  void ThrowDOMException(DOMExceptionCode, const std::string&) { raised = true; }
};
struct String {
  static std::string Number(size_t n) { return std::to_string(n); }
};
std::string StrCat(std::initializer_list<std::string> parts) {
  std::string result; for (auto& part : parts) result += part; return result;
}
struct ExceptionMessages {
  static constexpr int kInclusiveBound = 0;
  template<class... T> static std::string IndexOutsideRange(T...) { return ""; }
};
struct AudioBuffer {
  std::vector<Member<DOMFloat32Array>> channels_;
  uint32_t noise_channel_mask_ = 0;
  AudioBuffer(size_t channels=1, size_t frames=5000) {
    for (size_t i=0; i<channels; ++i)
      channels_.push_back({std::make_shared<DOMFloat32Array>(frames)});
  }
  size_t length() { return channels_[0].Get()->length(); }
  NotShared<DOMFloat32Array> getChannelData(unsigned, ExceptionState&);
  NotShared<DOMFloat32Array> getChannelData(unsigned);
  void copyFromChannel(NotShared<DOMFloat32Array>, int32_t, size_t, ExceptionState&);
  void Zero();
};
"""

CASES = r"""
int main() {
  using namespace blink;
  ExceptionState exceptions;
  AudioBuffer rendered;
  // SharedAudioBuffer takes the internal overload before rendering starts.
  auto shared = rendered.getChannelData(0);
  assert(rendered.noise_channel_mask_ == 0);
  assert(std::all_of(shared->samples.begin(), shared->samples.end(), [](float x){return x==0;}));
  // The offline renderer then overwrites the same shared backing store.
  std::fill(shared->samples.begin(), shared->samples.end(), 0.25f);
  auto first = rendered.getChannelData(0, exceptions)->samples;
  assert(first != std::vector<float>(5000,0.25f));
  auto expected = std::vector<float>(5000,0.25f);
  ApplyAudioFingerprintNoise(expected.data(),expected.size());
  assert(first == expected);
  assert(rendered.getChannelData(0, exceptions)->samples == first);
  DOMFloat32Array tail(500);
  rendered.copyFromChannel(NotShared<DOMFloat32Array>(&tail),0,4500,exceptions);
  assert(tail.samples == std::vector<float>(first.begin()+4500,first.end()));
  // copyFromChannel first must perturb the entire source with global indices.
  AudioBuffer copied;
  std::fill(copied.getChannelData(0)->samples.begin(),copied.getChannelData(0)->samples.end(),0.25f);
  copied.copyFromChannel(NotShared<DOMFloat32Array>(&tail),0,4500,exceptions);
  assert(tail.samples == std::vector<float>(expected.begin()+4500,expected.end()));
  assert(copied.getChannelData(0,exceptions)->samples == expected);
  copied.copyFromChannel(NotShared<DOMFloat32Array>(&tail),0,4500,exceptions);
  assert(copied.getChannelData(0,exceptions)->samples == expected);
  // Internal Zero also must not consume a fresh buffer's once-per-channel bit.
  AudioBuffer zeroed;
  zeroed.Zero();
  assert(zeroed.noise_channel_mask_ == 0);
  auto zeros = zeroed.getChannelData(0,exceptions)->samples;
  assert(zeros != std::vector<float>(5000,0));
  // Guard bits are independent, including the highest supported channel.
  AudioBuffer multichannel(32,16);
  auto ch0 = multichannel.getChannelData(0,exceptions)->samples;
  assert(multichannel.noise_channel_mask_ == 1);
  assert(multichannel.getChannelData(31)->samples == std::vector<float>(16,0));
  assert(multichannel.getChannelData(31,exceptions)->samples == ch0);
  assert(multichannel.noise_channel_mask_ == 0x80000001u);
  assert(!multichannel.getChannelData(32,exceptions));
  assert(exceptions.raised);
  exceptions.raised = false;
  multichannel.copyFromChannel(NotShared<DOMFloat32Array>(&tail),-1,0,exceptions);
  assert(exceptions.raised);
  assert(multichannel.noise_channel_mask_ == 0x80000001u);
  fingerprint::noise_enabled = false;
  AudioBuffer disabled;
  assert(disabled.getChannelData(0,exceptions)->samples == std::vector<float>(5000,0));
  AudioBuffer disabled_copy;
  disabled_copy.copyFromChannel(NotShared<DOMFloat32Array>(&tail),0,0,exceptions);
  assert(tail.samples == std::vector<float>(500,0));
  std::cout << "PASS: extracted C++ namespace, internal-sharing/render/read lifecycle, "
               "repeat reads, copy-first offset, Zero, channel guards, errors and noise-off\n";
}
"""


def main():
    if not (BASE / WEB_AUDIO / "audio_buffer.cc").exists():
        raise SystemExit("Run tools/verify.sh first to fetch the pinned Chromium files.")
    with tempfile.TemporaryDirectory(prefix="stealth-audio-regression-") as temp:
        work = Path(temp)
        for filename in ["audio_buffer.cc", "audio_buffer.h"]:
            destination = work / WEB_AUDIO / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(BASE / WEB_AUDIO / filename, destination)
        subprocess.run(["git", "apply", str(ROOT / "series/0015-audio-noise.patch")], cwd=work, check=True)
        source = (work / WEB_AUDIO / "audio_buffer.cc").read_text()
        # Preserve the helper's actual namespace scope. Never add an alias.
        helper = source[source.index("namespace {"):source.index("namespace blink {")]
        core = "\n".join(line[1:] for line in (ROOT / "series/0001-fingerprint-core.patch").read_text().splitlines() if line.startswith("+") and not line.startswith("+++"))
        hashes = "\n".join(function(core, f"inline uint64_t {name}(") for name in ["Hash", "Mix", "HashFor"])
        methods = "\n".join(function(source, signature) for signature in [
            "NotShared<DOMFloat32Array> AudioBuffer::getChannelData(\n",
            "NotShared<DOMFloat32Array> AudioBuffer::getChannelData(unsigned",
            "void AudioBuffer::copyFromChannel(NotShared<DOMFloat32Array> destination,\n                                  int32_t channel_number,\n                                  size_t buffer_offset,",
            "void AudioBuffer::Zero()",
        ])
        # Check the stock sharing path still uses the internal overload.
        shared = function(source, "SharedAudioBuffer::SharedAudioBuffer(")
        assert "buffer->getChannelData(i)->buffer()->ShareNonSharedForInternalUse(" in shared
        cpp = work / "audio-regression.cpp"
        cpp.write_text(ADAPTERS + hashes + "\n}\n" + helper + BLINK + methods + "\n}\n" + CASES)
        binary = work / "audio-regression"
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
