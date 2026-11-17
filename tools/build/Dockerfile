# Docker image for building Chromium with fp-patches.
#
# Usage:
#   docker build -t fp-chromium-builder -f tools/build/Dockerfile tools/build
#   docker run --rm -v "$PWD:/fp" -v ~/chromium-cache:/cache \
#     fp-chromium-builder /fp/tools/build.sh /cache/chromium --ungoogled
#
# Mirrors Chromium's install-build-deps.sh (Ubuntu) package list. The
# image only provides the environment; source, depot_tools and output
# live on the mounted volumes so builds survive container restarts.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    DEPOT_TOOLS_UPDATE=0

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git python3 python3-pip build-essential \
      pkg-config lsb-release file perl wget \
      libasound2-dev libatk1.0-dev libatk-bridge2.0-dev libatspi2.0-dev \
      libbluetooth-dev libbrotli-dev libbz2-dev libc6-dev libcairo2-dev \
      libcups2-dev libcurl4-gnutls-dev libdrm-dev libedit-dev libegl1-mesa-dev \
      libevdev-dev libffi-dev libfontconfig1-dev libfreetype6-dev \
      libgbm-dev libgl1-mesa-dev libglib2.0-dev libgtk-3-dev libgudev-1.0-dev \
      libharfbuzz-dev libicu-dev libjpeg-dev libkrb5-dev libnspr4-dev \
      libnss3-dev libpango1.0-dev libpci-dev libpipewire-0.3-dev \
      libpng-dev libproc2-dev libprotobuf-dev libpulse-dev libsctp-dev \
      libsecret-1-dev libsnappy-dev libsqlite3-dev libudev-dev libunity-dev \
      libusb-1.0-0-dev libva-dev libwayland-dev libwebp-dev libx11-xcb-dev \
      libxcomposite-dev libxcursor-dev libxdamage-dev libxext-dev \
      libxfixes-dev libxi-dev libxkbcommon-dev libxrandr-dev libxshmfence-dev \
      libxslt1-dev libxt-dev libxtst-dev mesa-common-dev \
      libunwind-dev libyuv-dev \
      fonts-noto-color-emoji fonts-freefont-ttf fonts-unifont \
      fonts-ipafont-gothic fonts-wqy-zenhei fonts-tlwg-loma-otf \
      xvfb \
    && rm -rf /var/lib/apt/lists/*

# Python deps for depot_tools/gn/ninja tooling and the smoke test.
RUN pip3 install --break-system-packages --no-cache-dir websockets

# Modern node is needed by the build (apt's is too old).
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
